import json

from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register

TERMINAL = {"completed": "Completed", "busy": "Busy", "failed": "Failed", "no-answer": "No Answer",
	"canceled": "Canceled", "in-progress": "In Progress", "ringing": "Ringing"}


@register
class ExotelProvider(BaseProvider):
	vendor = "Exotel"
	slug = "exotel"
	label = "Exotel"
	category = "Cloud CPaaS"
	color = "#1F6FEB"
	region = "India · SEA · Middle East"
	sort_order = 25
	tagline = "Cloud calling for India, SEA & the Gulf"
	description = "Click-to-call via ExoPhones, dynamic agent routing through the Connect applet, Passthru events and SMS."
	capabilities = frozenset({"outbound", "inbound", "events", "sms", "recording", "numbers"})
	fields = [
		F("subdomain", "API Subdomain", reqd=True, default="api.exotel.com",
			description="api.exotel.com (Singapore) or api.in.exotel.com (Mumbai)"),
		F("account_sid", "Account SID", reqd=True),
		F("api_key", "API Key", reqd=True),
		F("api_token", "API Token", "Password", secret=True, reqd=True),
		F("default_caller_id", "Default ExoPhone (Caller ID)", reqd=True),
		F("sms_sender", "SMS Sender ID"),
		F("record_calls", "Record calls", "Check"),
	]

	def _base(self, version="v1"):
		self.require("subdomain", "account_sid", "api_key", "api_token")
		return f"https://{self.cfg('subdomain')}/{version}/Accounts/{self.cfg('account_sid')}"

	def _auth(self):
		return (self.cfg("api_key"), self.secret("api_token"))

	def test_connection(self):
		res = self.http("GET", f"{self._base()}.json", auth=self._auth())
		acc = res.get("Account") or {}
		return {"ok": True, "message": _("Connected: {0} ({1})").format(acc.get("FriendlyName") or acc.get("Sid"), acc.get("Status"))}

	def make_call(self, agent, to, caller_id, call_name):
		if not agent.get("mobile_no"):
			raise ProviderError(_("Exotel rings the agent's mobile first — set a mobile number on the Voice Agent."))
		res = self.http("POST", f"{self._base()}/Calls/connect.json", auth=self._auth(), data={
			"From": agent["mobile_no"],
			"To": to,
			"CallerId": caller_id or self.cfg("default_caller_id"),
			"Record": "true" if self.flag("record_calls") else "false",
			"StatusCallback": self.webhook_url("status", nt=call_name),
			"StatusCallbackContentType": "application/json",
			"StatusCallbackEvents[0]": "terminal",
			"StatusCallbackEvents[1]": "answered",
		})
		return (res.get("Call") or {}).get("Sid")

	def send_sms(self, to, body, sender=None):
		res = self.http("POST", f"{self._base()}/Sms/send.json", auth=self._auth(), data={
			"From": sender or self.cfg("sms_sender") or self.cfg("default_caller_id"), "To": to, "Body": body})
		return {"id": (res.get("SMSMessage") or {}).get("Sid"), "status": "Sent"}

	def list_numbers(self):
		res = self.http("GET", f"{self._base('v2_beta')}/IncomingPhoneNumbers", auth=self._auth())
		return [{"number": n.get("phone_number") or n.get("friendly_name"), "label": n.get("friendly_name")}
			for n in res.get("incoming_phone_numbers", [])]

	def fetch_recording(self, url):
		return self.http("GET", url, expect_json=False, auth=self._auth(), timeout=120).content

	def handle_webhook(self, phase, p, request):
		if phase == "connect":
			target = self.route_inbound(p.get("CallTo") or p.get("To"), p.get("CallFrom") or p.get("From"))
			numbers = [a.mobile_no for a in target.ring_list() if a.mobile_no] if target.kind == "agents" else (
				[target.number] if target.kind == "number" else [])
			ev = CallEvent(external_id=p.get("CallSid"), kind="ringing", direction="Inbound",
				from_number=p.get("CallFrom"), to_number=p.get("CallTo") or p.get("To"),
				ring_agents=target.users, queue=target.queue, route=target.route, raw=p)
			body = {
				"fetch_after_attempt": False,
				"destination": {"numbers": numbers},
				"record": bool(target.record),
				"max_ringing_duration": target.timeout,
				"outgoing_phone_number": p.get("CallTo") or p.get("To"),
			}
			return WebhookResult([ev], json.dumps(body), "application/json")
		if phase == "passthru":
			return WebhookResult([self._passthru(p)], "OK")
		if phase == "status":
			status = TERMINAL.get((p.get("Status") or "").lower())
			if (p.get("EventType") or "").lower() == "answered":
				status = "In Progress"
			kind = "answered" if status == "In Progress" else "ended" if status in ("Completed", "Busy", "Failed",
				"No Answer", "Canceled") else "status"
			ev = CallEvent(external_id=p.get("CallSid"), local_ref=p.get("nt"), kind=kind, status=status,
				duration=p.get("ConversationDuration") or p.get("Duration"), started_at=p.get("StartTime"),
				ended_at=p.get("EndTime"), recording_url=p.get("RecordingUrl") or None, raw=p)
			return WebhookResult([ev], "OK")
		return WebhookResult()

	def _passthru(self, p):
		ct = (p.get("CallType") or "").lower()
		dcs = (p.get("DialCallStatus") or p.get("Status") or "").lower()
		status = None
		if ct == "completed" or dcs == "completed":
			status = "Completed"
		elif ct == "voicemail":
			status = "Voicemail"
		elif dcs in TERMINAL and dcs not in ("in-progress", "ringing"):
			status = TERMINAL[dcs]
		elif ct == "call-attempt":
			status = "Ringing"
		rec = p.get("RecordingUrl")
		return CallEvent(
			external_id=p.get("CallSid"), kind="ended" if status in ("Completed", "Voicemail") else "status",
			status=status, direction="Inbound" if (p.get("Direction") or "incoming").startswith("in") else "Outbound",
			from_number=p.get("CallFrom"), to_number=p.get("CallTo") or p.get("To"),
			agent_hint=p.get("DialWhomNumber") or p.get("AgentEmail"),
			duration=p.get("DialCallDuration") or p.get("ConversationDuration"),
			started_at=p.get("StartTime"), ended_at=p.get("EndTime"),
			recording_url=rec if rec and rec != "null" else None, raw=p)

	def webhooks(self):
		return [
			(_("Connect applet — dynamic URL"), "connect", _("Lets Neotec decide which agents to ring (queues, schedules, presence).")),
			(_("Passthru applet — after Connect"), "passthru", _("Reports outcome, duration, agent and recording.")),
			(_("Status callback"), "status", _("Set automatically on click-to-call.")),
		]

	def setup_steps(self):
		return [
			_("Exotel Dashboard › API settings: copy Account SID, API Key and API Token."),
			_("Pick the API subdomain matching your cluster (Singapore or Mumbai)."),
			_("App Bazaar: build a flow → Connect applet → 'Dial whom: Dynamic URL' → paste the Connect URL."),
			_("Add a Passthru applet after Connect and paste the Passthru URL (Make Passthru async: No)."),
			_("Assign the flow to your ExoPhone; enter that ExoPhone as Default Caller ID."),
			_("Agents need a mobile number — Exotel bridges calls through mobiles."),
		]
