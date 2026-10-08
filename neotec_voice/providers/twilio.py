import base64
import hashlib
import hmac
import time

import frappe
from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.phone import to_e164
from neotec_voice.core.util import setting
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register, x, xml_doc

API = "https://api.twilio.com/2010-04-01"
STATUS = {
	"queued": "Queued", "initiated": "Initiated", "ringing": "Ringing", "in-progress": "In Progress",
	"answered": "In Progress", "completed": "Completed", "busy": "Busy", "no-answer": "No Answer",
	"canceled": "Canceled", "failed": "Failed",
}
LEG_EVENTS = "initiated ringing answered completed"


def safe_identity(value):
	return str(value or "").replace("@", "(at)")


@register
class TwilioProvider(BaseProvider):
	vendor = "Twilio"
	slug = "twilio"
	label = "Twilio"
	category = "Cloud CPaaS"
	color = "#F22F46"
	sort_order = 20
	tagline = "Global programmable voice & SMS"
	description = "Numbers in 100+ countries, browser softphone via Voice SDK, signed webhooks and SMS."
	capabilities = frozenset({"outbound", "inbound", "events", "sms", "recording", "softphone", "numbers", "hangup", "provision"})
	fields = [
		F("account_sid", "Account SID", reqd=True, placeholder="AC..."),
		F("auth_token", "Auth Token", "Password", secret=True, reqd=True),
		F("default_caller_id", "Default Caller ID (E.164)", placeholder="+9665..."),
		F("sms_sender", "SMS Sender (number or alphanumeric ID)"),
		F("api_key", "API Key SID (for browser softphone)", placeholder="SK..."),
		F("api_secret", "API Key Secret", "Password", secret=True),
		F("twiml_app_sid", "TwiML App SID (for browser softphone)", placeholder="AP..."),
		F("record_calls", "Record calls", "Check"),
		F("verify_signature", "Verify X-Twilio-Signature", "Check", default=1),
		F("tts_voice_ar", "Arabic TTS voice", default="Google.ar-XA-Standard-A"),
	]

	# ---------------------------------------------------------------- utils
	def _auth(self):
		self.require("account_sid", "auth_token")
		return (self.cfg("account_sid"), self.secret("auth_token"))

	def _url(self, path):
		return f"{API}/Accounts/{self.cfg('account_sid')}/{path}"

	def _agent_endpoint(self, agent):
		ident = agent.get("identity") or ""
		if str(ident).startswith("sip:"):
			return ident
		if agent.get("device") == "Softphone":
			return f"client:{safe_identity(agent.get('user'))}"
		if not agent.get("mobile_no"):
			raise ProviderError(_("Agent {0} has no mobile number for call-back.").format(agent.get("user")))
		return agent.get("mobile_no")

	def _say(self, text, text_ar=None):
		out = []
		if text:
			out.append(x("Say", text))
		if text_ar:
			out.append(x("Say", text_ar, voice=self.cfg("tts_voice_ar"), language="ar-XA"))
		return out

	# ------------------------------------------------------------- contract
	def test_connection(self):
		res = self.http("GET", self._url(".json").replace("/.json", ".json"), auth=self._auth())
		return {"ok": True, "message": _("Connected to {0} ({1})").format(res.get("friendly_name"), res.get("status"))}

	def make_call(self, agent, to, caller_id, call_name):
		cid = caller_id or self.cfg("default_caller_id")
		if not cid:
			raise ProviderError(_("Set a Default Caller ID for Twilio."))
		data = [
			("To", self._agent_endpoint(agent)),
			("From", cid),
			("Url", self.webhook_url("bridge", nt=call_name, to=to, cid=cid)),
			("Method", "POST"),
			("StatusCallback", self.webhook_url("status", nt=call_name)),
			("StatusCallbackMethod", "POST"),
		] + [("StatusCallbackEvent", e) for e in LEG_EVENTS.split()]
		res = self.http("POST", self._url("Calls.json"), data=data, auth=self._auth())
		return res.get("sid")

	def hangup(self, call):
		self.http("POST", self._url(f"Calls/{call.external_id}.json"), data={"Status": "completed"}, auth=self._auth())

	def send_sms(self, to, body, sender=None):
		res = self.http("POST", self._url("Messages.json"), auth=self._auth(), data={
			"To": to, "From": sender or self.cfg("sms_sender") or self.cfg("default_caller_id"), "Body": body})
		return {"id": res.get("sid"), "status": "Sent"}

	def list_numbers(self):
		res = self.http("GET", self._url("IncomingPhoneNumbers.json?PageSize=200"), auth=self._auth())
		return [{"number": n.get("phone_number"), "label": n.get("friendly_name"), "vendor_id": n.get("sid")}
			for n in res.get("incoming_phone_numbers", [])]

	def provision(self):
		"""Point every number on the account at the Neotec voice webhook."""
		res = self.http("GET", self._url("IncomingPhoneNumbers.json?PageSize=200"), auth=self._auth())
		done = 0
		for n in res.get("incoming_phone_numbers", []):
			self.http("POST", self._url(f"IncomingPhoneNumbers/{n['sid']}.json"), auth=self._auth(), data={
				"VoiceUrl": self.webhook_url("voice"), "VoiceMethod": "POST",
				"StatusCallback": self.webhook_url("status"), "StatusCallbackMethod": "POST"})
			done += 1
		return {"ok": True, "message": _("{0} numbers now route through Neotec").format(done)}

	def fetch_recording(self, url):
		return self.http("GET", url, expect_json=False, auth=self._auth(), timeout=120).content

	def verify_signature(self, request, payload):
		if not self.flag("verify_signature", 1):
			return True
		sig = request.headers.get("X-Twilio-Signature")
		token = self.secret("auth_token")
		if not (sig and token):
			return False
		params = request.form.to_dict() if request.method == "POST" else {}
		data = "".join(f"{k}{params[k]}" for k in sorted(params))
		for url in {request.url, request.url.replace("http://", "https://", 1)}:
			mac = base64.b64encode(hmac.new(token.encode(), (url + data).encode(), hashlib.sha1).digest()).decode()
			if hmac.compare_digest(mac, sig):
				return True
		return False

	def webrtc_config(self, agent_doc):
		if not (self.cfg("api_key") and self.secret("api_secret") and self.cfg("twiml_app_sid")):
			return None
		import jwt

		now = int(time.time())
		token = jwt.encode(
			{
				"jti": f"{self.cfg('api_key')}-{now}",
				"iss": self.cfg("api_key"),
				"sub": self.cfg("account_sid"),
				"nbf": now,
				"exp": now + 3600,
				"grants": {
					"identity": safe_identity(agent_doc.name),
					"voice": {"incoming": {"allow": True}, "outgoing": {"application_sid": self.cfg("twiml_app_sid")}},
				},
			},
			self.secret("api_secret"),
			algorithm="HS256",
			headers={"cty": "twilio-fpa;v=1"},
		)
		return {"kind": "twilio", "token": token, "identity": safe_identity(agent_doc.name),
			"lib_url": setting("twilio_sdk_url")}

	# ------------------------------------------------------------- webhooks
	def handle_webhook(self, phase, p, request):
		if phase == "voice":
			return self._voice(p)
		if phase == "bridge":
			dest, cid = p.get("to"), p.get("cid")
			twiml = x("Response", [x("Dial", [x("Number", dest, statusCallbackEvent=LEG_EVENTS,
				statusCallback=self.webhook_url("status", nt=p.get("nt")))], callerId=cid,
				record="record-from-answer-dual" if self.flag("record_calls") else None,
				recordingStatusCallback=self.webhook_url("recording", nt=p.get("nt")))])
			ev = CallEvent(external_id=p.get("CallSid"), local_ref=p.get("nt"), kind="ringing", direction="Outbound", raw=p)
			return WebhookResult([ev], xml_doc(twiml), "text/xml")
		if phase == "dial_result":
			if p.get("DialCallStatus") in ("completed", "answered"):
				return WebhookResult([], xml_doc(x("Response", [x("Hangup")])), "text/xml")
			from neotec_voice.core.routing import voicemail_target

			return WebhookResult([], xml_doc(self._target_twiml(voicemail_target(), p.get("From"))), "text/xml")
		if phase == "hangup":
			return WebhookResult([], xml_doc(x("Response", [x("Hangup")])), "text/xml")
		if phase == "status":
			return WebhookResult([self._status_event(p)], "", "text/plain")
		if phase == "recording":
			url = p.get("RecordingUrl")
			ev = CallEvent(
				external_id=p.get("ParentCallSid") or p.get("CallSid"), local_ref=p.get("nt"),
				kind="voicemail" if p.get("vm") else "recording",
				recording_url=f"{url}.mp3" if url and not url.endswith(".mp3") else url, raw=p)
			return WebhookResult([ev], "", "text/plain")
		return WebhookResult()

	def _voice(self, p):
		caller = p.get("Caller") or p.get("From") or ""
		if caller.startswith("client:"):
			dest = to_e164(p.get("To"))
			cid = p.get("CallerId") or self.cfg("default_caller_id")
			ev = CallEvent(external_id=p.get("CallSid"), local_ref=p.get("nt"), kind="initiated", direction="Outbound",
				from_number=cid, to_number=dest, agent_hint=caller, raw=p)
			twiml = x("Response", [x("Dial", [x("Number", dest, statusCallbackEvent=LEG_EVENTS,
				statusCallback=self.webhook_url("status"))], callerId=cid,
				record="record-from-answer-dual" if self.flag("record_calls") else None,
				recordingStatusCallback=self.webhook_url("recording"))])
			return WebhookResult([ev], xml_doc(twiml), "text/xml")
		target = self.route_inbound(p.get("To"), p.get("From"))
		ev = CallEvent(external_id=p.get("CallSid"), kind="ringing", direction="Inbound", from_number=p.get("From"),
			to_number=p.get("To"), ring_agents=target.users, queue=target.queue, route=target.route, raw=p)
		return WebhookResult([ev], xml_doc(self._target_twiml(target, p.get("From"))), "text/xml")

	def _target_twiml(self, target, caller):
		if target.kind == "agents":
			nouns = []
			for a in target.ring_list():
				cb = {"statusCallbackEvent": LEG_EVENTS, "statusCallback": self.webhook_url("status")}
				if a.device == "Softphone":
					nouns.append(x("Client", safe_identity(a.user), **cb))
				elif str(a.identity or "").startswith("sip:"):
					nouns.append(x("Sip", a.identity, **cb))
				elif a.mobile_no:
					nouns.append(x("Number", a.mobile_no, **cb))
			dial = x("Dial", nouns, timeout=target.timeout, action=self.webhook_url("dial_result"),
				record="record-from-answer-dual" if target.record else None,
				recordingStatusCallback=self.webhook_url("recording") if target.record else None)
			return x("Response", self._say(target.message, target.message_ar) + [dial])
		if target.kind == "number":
			return x("Response", [x("Dial", [x("Number", target.number)], callerId=caller)])
		if target.kind == "voicemail":
			return x("Response", self._say(target.message, target.message_ar) + [
				x("Record", maxLength=120, playBeep=True, action=self.webhook_url("hangup"),
					recordingStatusCallback=self.webhook_url("recording", vm=1)),
			])
		return x("Response", self._say(target.message, target.message_ar) + [x("Hangup")])

	def _status_event(self, p):
		raw = p.get("CallStatus") or p.get("DialCallStatus") or ""
		status = STATUS.get(raw)
		is_child = bool(p.get("ParentCallSid"))
		if not is_child and p.get("Direction") == "inbound" and status == "In Progress":
			status = "Ringing"  # the caller reached our IVR; no agent yet
		kind = "answered" if status == "In Progress" else ("ended" if status in ("Completed", "Busy", "No Answer",
			"Canceled", "Failed") else "status")
		return CallEvent(
			external_id=p.get("ParentCallSid") or p.get("CallSid"), local_ref=p.get("nt"), kind=kind, status=status,
			duration=p.get("CallDuration"), agent_hint=p.get("To") if is_child else None,
			recording_url=p.get("RecordingUrl"), raw=p)

	# ----------------------------------------------------------- setup page
	def webhooks(self):
		return [
			(_("Voice URL — numbers & TwiML App"), "voice", _("HTTP POST. Set on each number (or use Provision) and on the TwiML App.")),
			(_("Status callback (optional on numbers)"), "status", _("HTTP POST. Neotec sets this automatically on calls it places.")),
		]

	def setup_steps(self):
		return [
			_("Console › Account › API keys & tokens: copy the Account SID and Auth Token into Credentials."),
			_("Buy or port a number; enter it as Default Caller ID."),
			_("Save, then press Provision to point all numbers' Voice URL at Neotec (or paste the Voice URL manually)."),
			_("Browser softphone: create an API Key (Standard) and a TwiML App whose Voice URL is the Neotec Voice URL; paste the SIDs here."),
			_("Map agents on the Agents tab — softphone agents ring as Twilio clients, others ring their mobile."),
			_("Keep 'Verify X-Twilio-Signature' on in production."),
		]
