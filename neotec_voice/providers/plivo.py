from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register, x, xml_doc

API = "https://api.plivo.com/v1/Account"
HANGUP = {"completed": "Completed", "busy": "Busy", "no-answer": "No Answer", "failed": "Failed",
	"cancel": "Canceled", "timeout": "No Answer"}


@register
class PlivoProvider(BaseProvider):
	vendor = "Plivo"
	slug = "plivo"
	label = "Plivo"
	category = "Cloud CPaaS"
	color = "#2DB46D"
	sort_order = 35
	tagline = "Cost-efficient voice & SMS API"
	description = "Click-to-call via agent call-back, inbound routing rendered as Plivo XML, dial callbacks, recordings and SMS."
	capabilities = frozenset({"outbound", "inbound", "events", "sms", "recording", "numbers", "hangup"})
	fields = [
		F("auth_id", "Auth ID", reqd=True),
		F("auth_token", "Auth Token", "Password", secret=True, reqd=True),
		F("default_caller_id", "Default Caller ID", reqd=True),
		F("sms_sender", "SMS Sender"),
		F("record_calls", "Record calls", "Check"),
	]

	def _url(self, path):
		self.require("auth_id", "auth_token")
		return f"{API}/{self.cfg('auth_id')}/{path}"

	def _auth(self):
		return (self.cfg("auth_id"), self.secret("auth_token"))

	def test_connection(self):
		res = self.http("GET", self._url(""), auth=self._auth())
		return {"ok": True, "message": _("Connected: {0} (credit {1})").format(res.get("name"), res.get("cash_credits"))}

	def make_call(self, agent, to, caller_id, call_name):
		leg = agent.get("identity") if str(agent.get("identity") or "").startswith("sip:") else agent.get("mobile_no")
		if not leg:
			raise ProviderError(_("Agent needs a mobile number or a sip: identity for Plivo."))
		cid = caller_id or self.cfg("default_caller_id")
		res = self.http("POST", self._url("Call/"), auth=self._auth(), json={
			"from": cid, "to": leg, "answer_url": self.webhook_url("bridge", nt=call_name, to=to, cid=cid),
			"answer_method": "POST", "hangup_url": self.webhook_url("hangup", nt=call_name), "hangup_method": "POST"})
		return res.get("request_uuid")

	def hangup(self, call):
		self.http("DELETE", self._url(f"Call/{call.vendor_ref or call.external_id}/"), auth=self._auth(), expect_json=False)

	def send_sms(self, to, body, sender=None):
		res = self.http("POST", self._url("Message/"), auth=self._auth(), json={
			"src": sender or self.cfg("sms_sender") or self.cfg("default_caller_id"), "dst": to.lstrip("+"), "text": body})
		return {"id": (res.get("message_uuid") or [None])[0], "status": "Sent"}

	def list_numbers(self):
		res = self.http("GET", self._url("Number/?limit=20"), auth=self._auth())
		return [{"number": "+" + str(n.get("number")).lstrip("+"), "label": n.get("alias")} for n in res.get("objects", [])]

	def fetch_recording(self, url):
		return self.http("GET", url, expect_json=False, auth=self._auth(), timeout=120).content

	def _dial(self, children, **attrs):
		return x("Dial", children, callbackUrl=self.webhook_url("dial", nt=attrs.pop("nt", None)), callbackMethod="POST", **attrs)

	def handle_webhook(self, phase, p, request):
		ext = p.get("RequestUUID") if p.get("nt") else None
		ext = ext or p.get("CallUUID")
		if phase == "answer":
			target = self.route_inbound(p.get("To"), p.get("From"))
			body = []
			if target.message:
				body.append(x("Speak", target.message))
			if target.message_ar:
				body.append(x("Speak", target.message_ar, language="arb"))
			if target.kind == "agents":
				if target.record:
					body.append(x("Record", action=self.webhook_url("recording"), startOnDialAnswer=True, redirect=False))
				nouns = [x("User", a.identity) if str(a.identity).startswith("sip:") else x("Number", (a.mobile_no or "").lstrip("+"))
					for a in target.ring_list()]
				body.append(self._dial(nouns, timeout=target.timeout))
			elif target.kind == "number":
				body.append(self._dial([x("Number", target.number.lstrip("+"))]))
			elif target.kind == "voicemail":
				body.append(x("Record", action=self.webhook_url("recording", vm=1), maxLength=120, playBeep=True))
			ev = CallEvent(external_id=p.get("CallUUID"), kind="ringing", direction="Inbound", from_number=p.get("From"),
				to_number=p.get("To"), ring_agents=target.users, queue=target.queue, route=target.route, raw=p)
			return WebhookResult([ev], xml_doc(x("Response", body)), "text/xml")
		if phase == "bridge":
			body = []
			if self.flag("record_calls"):
				body.append(x("Record", action=self.webhook_url("recording", nt=p.get("nt")), startOnDialAnswer=True, redirect=False))
			body.append(self._dial([x("Number", (p.get("to") or "").lstrip("+"))], callerId=p.get("cid"), nt=p.get("nt")))
			ev = CallEvent(external_id=ext, local_ref=p.get("nt"), kind="ringing", direction="Outbound",
				vendor_ref=p.get("CallUUID"), raw=p)
			return WebhookResult([ev], xml_doc(x("Response", body)), "text/xml")
		if phase == "dial":
			action = (p.get("DialAction") or "").lower()
			kind = "answered" if action in ("answer", "connected") else "status"
			ev = CallEvent(external_id=p.get("DialALegUUID") or ext, local_ref=p.get("nt"), kind=kind,
				agent_hint=p.get("DialBLegTo") if kind == "answered" and not p.get("nt") else None, raw=p)
			return WebhookResult([ev], "OK")
		if phase == "hangup":
			status = HANGUP.get((p.get("CallStatus") or "").lower(), "Completed")
			ev = CallEvent(external_id=ext, local_ref=p.get("nt"), kind="ended", status=status,
				duration=p.get("Duration"), talk_time=p.get("BillDuration"), started_at=p.get("StartTime"),
				answered_at=p.get("AnswerTime") or None, ended_at=p.get("EndTime"), hangup_cause=p.get("HangupCause"),
				vendor_cost=p.get("TotalCost"), raw=p)
			return WebhookResult([ev], "OK")
		if phase == "recording":
			ev = CallEvent(external_id=ext, local_ref=p.get("nt"), kind="voicemail" if p.get("vm") else "recording",
				recording_url=p.get("RecordUrl"), raw=p)
			return WebhookResult([ev], xml_doc(x("Response", [x("Hangup")])) if p.get("vm") else "OK",
				"text/xml" if p.get("vm") else "text/plain")
		return WebhookResult()

	def webhooks(self):
		return [
			(_("Answer URL (Application)"), "answer", _("POST")),
			(_("Hangup URL (Application)"), "hangup", _("POST")),
		]

	def setup_steps(self):
		return [
			_("Plivo Console › Overview: copy Auth ID and Auth Token."),
			_("Voice › Applications › XML › Add new: paste Answer URL and Hangup URL; attach your numbers to the application."),
			_("Enter your Plivo number as Default Caller ID. Save and Test."),
			_("Agents ring their mobile, or a sip: endpoint if you set one as their identity on the Agents tab."),
		]
