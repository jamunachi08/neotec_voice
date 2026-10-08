import json
import time
import uuid

from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.phone import national_digits
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register

API = "https://api.nexmo.com"
STATUS = {"started": "Initiated", "ringing": "Ringing", "answered": "In Progress", "completed": "Completed",
	"busy": "Busy", "timeout": "No Answer", "unanswered": "No Answer", "rejected": "Busy", "failed": "Failed",
	"cancelled": "Canceled", "machine": "In Progress"}


@register
class VonageProvider(BaseProvider):
	vendor = "Vonage"
	slug = "vonage"
	label = "Vonage (Voice API)"
	category = "Cloud CPaaS"
	color = "#871FFF"
	sort_order = 30
	tagline = "Global voice API with NCCO call control"
	description = "Click-to-call and inbound routing rendered as NCCO, JWT-authenticated API, recordings and SMS."
	capabilities = frozenset({"outbound", "inbound", "events", "sms", "recording"})
	fields = [
		F("application_id", "Application ID", reqd=True),
		F("private_key", "Application private key (PEM)", "Password", secret=True, reqd=True,
			description="Paste the whole PEM including BEGIN/END lines"),
		F("default_caller_id", "Default Caller ID (LVN)", reqd=True),
		F("api_key", "API Key (SMS / test)"),
		F("api_secret", "API Secret", "Password", secret=True),
		F("record_calls", "Record calls", "Check"),
	]

	def _jwt(self):
		import jwt

		self.require("application_id", "private_key")
		now = int(time.time())
		return jwt.encode({"application_id": self.cfg("application_id"), "iat": now, "exp": now + 900,
			"jti": str(uuid.uuid4())}, self.secret("private_key"), algorithm="RS256")

	def _h(self):
		return {"Authorization": f"Bearer {self._jwt()}"}

	def test_connection(self):
		self._jwt()
		if self.cfg("api_key") and self.secret("api_secret"):
			res = self.http("GET", f"{API}/v2/applications/{self.cfg('application_id')}",
				auth=(self.cfg("api_key"), self.secret("api_secret")))
			return {"ok": True, "message": _("Application '{0}' reachable; JWT signing OK").format(res.get("name"))}
		return {"ok": True, "message": _("JWT signing OK (add API key/secret for a live check)")}

	def _endpoint(self, a):
		if a.get("device") == "Softphone" and a.get("identity"):
			return {"type": "app", "user": a["identity"]}
		if not a.get("mobile_no"):
			raise ProviderError(_("Agent {0} has no mobile number.").format(a.get("user")))
		return {"type": "phone", "number": national_digits(a["mobile_no"])}

	def make_call(self, agent, to, caller_id, call_name):
		cid = national_digits(caller_id or self.cfg("default_caller_id"))
		connect = {"action": "connect", "from": cid, "endpoint": [{"type": "phone", "number": national_digits(to)}],
			"eventUrl": [self.webhook_url("event", nt=call_name)]}
		ncco = ([self._record_action(call_name)] if self.flag("record_calls") else []) + [connect]
		res = self.http("POST", f"{API}/v1/calls", headers=self._h(), json={
			"to": [self._endpoint(agent)], "from": {"type": "phone", "number": cid}, "ncco": ncco,
			"event_url": [self.webhook_url("event", nt=call_name)]})
		return res.get("conversation_uuid") or res.get("uuid")

	def _record_action(self, nt=None):
		return {"action": "record", "eventUrl": [self.webhook_url("recording", nt=nt)], "split": "conversation", "channels": 2}

	def send_sms(self, to, body, sender=None):
		self.require("api_key", "api_secret")
		res = self.http("POST", "https://rest.nexmo.com/sms/json", data={"api_key": self.cfg("api_key"),
			"api_secret": self.secret("api_secret"), "from": sender or self.cfg("default_caller_id"),
			"to": national_digits(to), "text": body, "type": "unicode"})
		msg = (res.get("messages") or [{}])[0]
		if str(msg.get("status")) != "0":
			raise ProviderError(msg.get("error-text") or _("SMS rejected"))
		return {"id": msg.get("message-id"), "status": "Sent"}

	def fetch_recording(self, url):
		return self.http("GET", url, expect_json=False, headers=self._h(), timeout=120).content

	def handle_webhook(self, phase, p, request):
		if phase == "answer":
			target = self.route_inbound(p.get("to"), p.get("from"))
			ncco = []
			if target.message:
				ncco.append({"action": "talk", "text": target.message})
			if target.message_ar:
				ncco.append({"action": "talk", "text": target.message_ar, "language": "ar"})
			if target.kind == "agents":
				if target.record:
					ncco.append(self._record_action())
				ncco.append({"action": "connect", "from": national_digits(p.get("to")), "timeout": target.timeout,
					"endpoint": [self._endpoint(target.ring_list()[0])], "eventUrl": [self.webhook_url("event")]})
			elif target.kind == "number":
				ncco.append({"action": "connect", "from": national_digits(p.get("to")),
					"endpoint": [{"type": "phone", "number": national_digits(target.number)}]})
			elif target.kind == "voicemail":
				ncco.append({"action": "record", "endOnSilence": 3, "beepStart": True, "timeOut": 120,
					"eventUrl": [self.webhook_url("recording", vm=1)]})
			ev = CallEvent(external_id=p.get("conversation_uuid") or p.get("uuid"), kind="ringing", direction="Inbound",
				from_number=p.get("from"), to_number=p.get("to"), ring_agents=target.users, queue=target.queue,
				route=target.route, raw=p)
			return WebhookResult([ev], json.dumps(ncco), "application/json")
		if phase == "event":
			status = STATUS.get(p.get("status"))
			if p.get("direction") == "inbound" and status == "In Progress":
				status = "Ringing"  # caller connected to our NCCO, agent not yet
			kind = "answered" if status == "In Progress" else ("ended" if status in ("Completed", "Busy", "No Answer",
				"Failed", "Canceled") else "status")
			ev = CallEvent(external_id=p.get("conversation_uuid") or p.get("uuid"), local_ref=p.get("nt"), kind=kind,
				status=status, agent_hint=p.get("to") if p.get("direction") == "outbound" and kind == "answered" else None,
				duration=p.get("duration"), started_at=p.get("start_time"), ended_at=p.get("end_time"),
				vendor_cost=p.get("price"), vendor_ref=p.get("uuid"), raw=p)
			return WebhookResult([ev], "", "text/plain")
		if phase == "recording":
			ev = CallEvent(external_id=p.get("conversation_uuid"), local_ref=p.get("nt"),
				kind="voicemail" if p.get("vm") else "recording", recording_url=p.get("recording_url"), raw=p)
			return WebhookResult([ev], "", "text/plain")
		return WebhookResult()

	def webhooks(self):
		return [
			(_("Answer URL (Voice capability, GET or POST)"), "answer", ""),
			(_("Event URL (Voice capability, POST)"), "event", ""),
		]

	def setup_steps(self):
		return [
			_("Vonage Dashboard › Applications › Create: enable Voice, generate a key pair and download the private key."),
			_("Set the Answer URL and Event URL shown here; link your Vonage number to the application."),
			_("Paste the Application ID and the private key PEM; set the LVN as Default Caller ID. Save and Test."),
			_("For SMS add the account API key/secret."),
			_("Vonage connects one endpoint per action — Ring All queues ring the first available agent."),
		]
