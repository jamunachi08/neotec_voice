import json

import frappe
from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.util import loads, seconds
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register

CDR_STATUS = {"ANSWERED": "Completed", "NO ANSWER": "No Answer", "BUSY": "Busy", "FAILED": "Failed", "VOICEMAIL": "Voicemail"}
MEMBER_STATUS = {"ALERT": "Ringing", "RING": "Ringing", "ANSWER": "In Progress", "ANSWERED": "In Progress"}


@register
class YeastarProvider(BaseProvider):
	vendor = "Yeastar P-Series"
	slug = "yeastar"
	label = "Yeastar P-Series"
	category = "IP-PBX"
	color = "#0A66C2"
	sort_order = 4
	tagline = "Appliance / cloud PBX popular across the GCC"
	description = "Open API click-to-call, live call-state webhooks and CDR webhooks."
	capabilities = frozenset({"outbound", "events", "recording"})
	fields = [
		F("host", "PBX URL", reqd=True, placeholder="https://company.ras.yeastar.com"),
		F("client_id", "API Client ID", reqd=True),
		F("client_secret", "API Client Secret", "Password", secret=True, reqd=True),
	]

	def _base(self):
		self.require("host", "client_id", "client_secret")
		return self.cfg("host").rstrip("/") + "/openapi/v1.0"

	def _token(self, force=False):
		ck = f"ntv:yeastar:{self.account.name}:token"
		tok = None if force else frappe.cache.get_value(ck)
		if tok:
			return tok
		res = self.http("POST", f"{self._base()}/get_token", json={
			"username": self.cfg("client_id"), "password": self.secret("client_secret")})
		if str(res.get("errcode", 0)) != "0" or not res.get("access_token"):
			raise ProviderError(_("Yeastar token error: {0}").format(res.get("errmsg") or res))
		frappe.cache.set_value(ck, res["access_token"], expires_in_sec=max(60, int(res.get("access_token_expire_time") or 1800) - 60))
		return res["access_token"]

	def test_connection(self):
		self._token(force=True)
		return {"ok": True, "message": _("Access token issued by Yeastar.")}

	def make_call(self, agent, to, caller_id, call_name):
		ext = agent.get("identity") or agent.get("extension")
		if not ext:
			raise ProviderError(_("Agent has no Yeastar extension mapped."))
		res = self.http("POST", f"{self._base()}/call/dial", params={"access_token": self._token()},
			json={"caller": str(ext), "callee": to})
		if str(res.get("errcode", 0)) != "0":
			raise ProviderError(_("Yeastar dial error: {0}").format(res.get("errmsg") or res))
		return res.get("call_id")

	def handle_webhook(self, phase, p, request):
		etype = str(p.get("type") or "")
		msg = loads(p.get("msg"), p.get("msg") or {}) or {}
		if etype == "30012" or phase == "cdr":
			status = CDR_STATUS.get((msg.get("status") or "").upper(), "Completed")
			talk = seconds(msg.get("talk_duration"))
			direction = (msg.get("type") or msg.get("call_type") or "Inbound").title()
			ev = CallEvent(external_id=msg.get("call_id") or msg.get("uid"), kind="ended", status=status,
				direction=direction if direction in ("Inbound", "Outbound", "Internal") else "Inbound",
				from_number=msg.get("call_from"), to_number=msg.get("call_to"),
				agent_hint=msg.get("call_to") if direction == "Inbound" else msg.get("call_from"),
				duration=seconds(msg.get("call_duration")), talk_time=talk, started_at=msg.get("time_start"),
				recording_url=msg.get("recording") or None, raw=p)
			return WebhookResult([ev], json.dumps({"errcode": 0}), "application/json")
		if etype == "30011":
			events = []
			members = msg.get("members") or []
			ext_hint, status, direction, frm, to = None, None, None, None, None
			for m in members:
				for role, info in m.items():
					if role == "extension":
						s = MEMBER_STATUS.get((info.get("member_status") or "").upper())
						if s:
							status = s if status != "In Progress" else status
							ext_hint = info.get("number") if s == "In Progress" or not ext_hint else ext_hint
					elif role == "inbound":
						direction, frm, to = "Inbound", info.get("from"), info.get("to")
					elif role == "outbound":
						direction, frm, to = "Outbound", info.get("from"), info.get("to")
			if status:
				events.append(CallEvent(external_id=msg.get("call_id"), kind="answered" if status == "In Progress" else "ringing",
					status=status, direction=direction, from_number=frm, to_number=to, agent_hint=ext_hint, raw=p))
			return WebhookResult(events, json.dumps({"errcode": 0}), "application/json")
		return WebhookResult([], json.dumps({"errcode": 0}), "application/json")

	def webhooks(self):
		return [(_("Webhook URL (Call State Changed 30011 + New CDR 30012)"), "event", "")]

	def setup_steps(self):
		return [
			_("Yeastar web portal › Integrations › API: enable API, note Client ID / Client Secret and allow the Frappe server IP."),
			_("Under Webhook, paste the Neotec webhook URL and subscribe to 'Call State Changed' and 'New CDR'."),
			_("Fill the PBX URL and credentials here, Save and Test."),
			_("Map agent extensions on the Agents tab."),
			_("Firmware and event codes can differ by release — check the API guide of your build if events don't appear."),
		]
