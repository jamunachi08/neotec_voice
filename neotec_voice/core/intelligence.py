"""Post-call pipeline: pull recording, keep a private copy, transcribe and
summarise on *self-hosted* models only (PDPL residency allow-list)."""

import json
from urllib.parse import urlparse

import frappe
import requests
from frappe import _
from frappe.utils import cint

from neotec_voice.core.util import settings, split_csv

PROMPT = """You are a contact-centre quality analyst for a company in Saudi Arabia.
Read the call transcript and reply ONLY with JSON having keys:
"summary" (max 80 words, same language as the call; Arabic if the call is Arabic),
"sentiment" (one of "Positive","Neutral","Negative"),
"action_items" (array of short strings),
"language" ("ar" or "en" or other ISO code).
Transcript:
"""


def _check_residency(url):
	allowed = [h.lower() for h in split_csv(settings().ai_allowed_hosts)]
	host = (urlparse(url).hostname or "").lower()
	if not allowed or host not in allowed:
		raise frappe.ValidationError(
			_("AI endpoint host {0} is not in the residency allow-list (Voice Settings).").format(host)
		)


def post_call(call_name):
	from neotec_voice.providers import get_provider

	call = frappe.get_doc("Voice Call", call_name)
	s = settings()
	audio = None
	try:
		if call.recording_url and not call.recording_file and (cint(s.store_recordings_locally) or cint(s.ai_enabled)):
			acc = frappe.get_doc("Voice Provider Account", call.provider_account)
			audio = get_provider(acc).fetch_recording(call.recording_url)
			if audio and cint(s.store_recordings_locally):
				f = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"{call.name}.mp3",
						"attached_to_doctype": "Voice Call",
						"attached_to_name": call.name,
						"attached_to_field": "recording_file",
						"is_private": 1,
						"content": audio,
					}
				).insert(ignore_permissions=True)
				call.db_set("recording_file", f.file_url)
		if not cint(s.ai_enabled) or call.ai_status == "Done":
			return
		if audio is None and call.recording_file:
			audio = frappe.get_doc("File", {"file_url": call.recording_file}).get_content()
		if not audio:
			call.db_set("ai_status", "Skipped")
			return
		transcript = transcribe(audio, s)
		result = summarise(transcript, s)
		call.db_set(
			{
				"transcript": transcript,
				"summary": result.get("summary"),
				"sentiment": result.get("sentiment") if result.get("sentiment") in ("Positive", "Neutral", "Negative") else None,
				"action_items": "\n".join(f"- {a}" for a in result.get("action_items") or []),
				"ai_status": "Done",
			}
		)
		_comment_on_party(call, result)
	except Exception:
		call.db_set("ai_status", "Failed")
		frappe.log_error(title=f"Neotec Voice post-call {call_name}", message=frappe.get_traceback())


def transcribe(audio, s):
	_check_residency(s.stt_url)
	r = requests.post(
		s.stt_url,
		files={"file": ("call.mp3", audio, "audio/mpeg")},
		data={"model": s.stt_model or "whisper-1", "language": s.ai_language_hint or ""},
		timeout=600,
	)
	r.raise_for_status()
	return (r.json() or {}).get("text", "").strip()


def summarise(transcript, s):
	if not transcript:
		return {}
	base = (s.ollama_url or "").rstrip("/")
	_check_residency(base)
	r = requests.post(
		f"{base}/api/generate",
		json={"model": s.ollama_model or "qwen2.5:7b", "prompt": PROMPT + transcript[:24000], "stream": False, "format": "json"},
		timeout=600,
	)
	r.raise_for_status()
	try:
		return json.loads((r.json() or {}).get("response") or "{}")
	except ValueError:
		return {"summary": (r.json() or {}).get("response", "")[:1000]}


def _comment_on_party(call, result):
	if not (call.party_type and call.party and result.get("summary")):
		return
	try:
		doc = frappe.get_doc(call.party_type, call.party)
		doc.add_comment("Comment", f"<b>{_('Call summary')}</b> ({call.name}): {frappe.utils.escape_html(result['summary'])}")
	except Exception:
		pass
