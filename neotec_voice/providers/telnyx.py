import base64
import json

from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.phone import to_e164
from neotec_voice.providers.base import F, BaseProvider, ProviderError, WebhookResult, register

API = "https://api.telnyx.com/v2"
CAUSE = {"normal_clearing": "Completed", "user_busy": "Busy", "timeout": "No Answer", "originator_cancel": "Canceled",
	"call_rejected": "Busy", "unspecified": "Failed"}


def _state(data):
	return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def _unstate(value):
	try:
		return json.loads(base64.b64decode(value).decode()) if value else {}
	except Exception:
		return {}


@register
class TelnyxProvider(BaseProvider):
	vendor = "Telnyx"
	slug = "telnyx"
	label = "Telnyx"
	category = "Cloud CPaaS"
	color = "#00C08B"
	sort_order = 40
	tagline = "Carrier-owned network with Call Control"
	description = "Call Control API driven entirely by Neotec: answer, transfer, record; Ed25519-signed webhooks; SMS."
	capabilities = frozenset({"outbound", "inbound", "events", "sms", "recording", "hangup"})
	fields = [
		F("api_key", "API Key (v2)", "Password", secret=True, reqd=True),
		F("connection_id", "Call Control Application (connection) ID", reqd=True),
		F("default_caller_id", "Default Caller ID", reqd=True),
		F("messaging_profile_id", "Messaging Profile ID (SMS)"),
		F("public_key", "Webhook public key (base64)", description="Portal › Keys & Credentials › Public Key"),
		F("record_calls", "Record calls", "Check"),
	]

	def _h(self):
		self.require("api_key")
		return {"Authorization": f"Bearer {self.secret('api_key')}"}

	def _action(self, call_control_id, action, payload):
		return self.http("POST", f"{API}/calls/{call_control_id}/actions/{action}", headers=self._h(), json=payload)

	def test_connection(self):
		res = self.http("GET", f"{API}/balance", headers=self._h())
		d = res.get("data") or {}
		return {"ok": True, "message": _("Connected · balance {0} {1}").format(d.get("balance"), d.get("currency"))}

	def _endpoint(self, a):
		ident = str(a.get("identity") or "")
		if ident.startswith("sip:"):
			return ident
		if not a.get("mobile_no"):
			raise ProviderError(_("Agent {0} needs a mobile number or sip: identity.").format(a.get("user")))
		return a["mobile_no"]

	def make_call(self, agent, to, caller_id, call_name):
		cid = caller_id or self.cfg("default_caller_id")
		res = self.http("POST", f"{API}/calls", headers=self._h(), json={
			"connection_id": self.cfg("connection_id"), "to": self._endpoint(agent), "from": cid,
			"webhook_url": self.webhook_url("event"),
			"client_state": _state({"nt": call_name, "bridge_to": to, "cid": cid})})
		d = res.get("data") or {}
		return d.get("call_session_id") or d.get("call_control_id")

	def hangup(self, call):
		if call.vendor_ref:
			self._action(call.vendor_ref, "hangup", {})

	def send_sms(self, to, body, sender=None):
		payload = {"from": sender or self.cfg("default_caller_id"), "to": to, "text": body}
		if self.cfg("messaging_profile_id"):
			payload["messaging_profile_id"] = self.cfg("messaging_profile_id")
		res = self.http("POST", f"{API}/messages", headers=self._h(), json=payload)
		return {"id": (res.get("data") or {}).get("id"), "status": "Sent"}

	def fetch_recording(self, url):
		return self.http("GET", url, expect_json=False, timeout=120).content

	def verify_signature(self, request, payload):
		key = self.cfg("public_key")
		if not key:
			return True
		from cryptography.exceptions import InvalidSignature
		from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

		sig = request.headers.get("telnyx-signature-ed25519")
		ts = request.headers.get("telnyx-timestamp")
		if not (sig and ts):
			return False
		try:
			Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(
				base64.b64decode(sig), f"{ts}|".encode() + request.get_data())
			return True
		except (InvalidSignature, ValueError):
			return False

	def handle_webhook(self, phase, p, request):
		data = p.get("data") or {}
		etype = data.get("event_type") or ""
		pl = data.get("payload") or {}
		state = _unstate(pl.get("client_state"))
		ccid = pl.get("call_control_id")
		session = pl.get("call_session_id")
		base = dict(external_id=session, local_ref=state.get("nt"), vendor_ref=ccid, raw=p)
		events = []
		if etype == "call.initiated" and pl.get("direction") == "incoming":
			target = self.route_inbound(pl.get("to"), pl.get("from"))
			route = {"kind": target.kind, "msg": target.message, "rec": target.record,
				"to": self._endpoint(target.ring_list()[0]) if target.kind == "agents" else target.number}
			self._action(ccid, "answer", {"client_state": _state({"route": route})})
			events.append(CallEvent(kind="ringing", direction="Inbound", from_number=pl.get("from"), to_number=pl.get("to"),
				ring_agents=target.users, queue=target.queue, route=target.route, **base))
		elif etype == "call.answered":
			route = state.get("route")
			if route:  # inbound leg answered by us — now run the Neotec decision
				if route.get("msg"):
					self._action(ccid, "speak", {"payload": route["msg"], "voice": "female", "language": "en-US",
						"client_state": _state({"after_speak": route})})
				else:
					self._run(ccid, route, pl)
				events.append(CallEvent(kind="status", **base))
			elif state.get("bridge_to"):  # click-to-call: agent picked up
				self._action(ccid, "transfer", {"to": state["bridge_to"], "from": state.get("cid"),
					"webhook_url": self.webhook_url("event")})
				if self.flag("record_calls"):
					self._action(ccid, "record_start", {"format": "mp3", "channels": "dual"})
				events.append(CallEvent(kind="answered", **base))
			else:  # transferred agent leg answered
				events.append(CallEvent(kind="answered", agent_hint=pl.get("to"), **base))
		elif etype == "call.speak.ended" and state.get("after_speak"):
			self._run(ccid, state["after_speak"], pl)
		elif etype == "call.hangup":
			cause = (pl.get("hangup_cause") or "").lower()
			events.append(CallEvent(kind="ended", status=CAUSE.get(cause, "Completed"), hangup_cause=cause,
				started_at=pl.get("start_time"), ended_at=pl.get("end_time"), **base))
		elif etype == "call.recording.saved":
			urls = pl.get("recording_urls") or pl.get("public_recording_urls") or {}
			events.append(CallEvent(kind="recording", recording_url=urls.get("mp3") or urls.get("wav"), **base))
		return WebhookResult(events, "", "text/plain")

	def _run(self, ccid, route, pl):
		if route.get("kind") in ("agents", "number") and route.get("to"):
			if route.get("rec"):
				self._action(ccid, "record_start", {"format": "mp3", "channels": "dual"})
			self._action(ccid, "transfer", {"to": to_e164(route["to"]) if not str(route["to"]).startswith("sip:") else route["to"],
				"from": pl.get("from"), "timeout_secs": 30, "webhook_url": self.webhook_url("event")})
		elif route.get("kind") == "voicemail":
			self._action(ccid, "record_start", {"format": "mp3", "channels": "single", "play_beep": True, "max_length": 120})
		else:
			self._action(ccid, "hangup", {})

	def webhooks(self):
		return [(_("Call Control application webhook URL"), "event", _("API v2"))]

	def setup_steps(self):
		return [
			_("Mission Control › Voice › Call Control: create an application and paste the webhook URL (API v2)."),
			_("Assign your numbers to the application; copy the application (connection) ID."),
			_("Create an API key and copy the account public key for signature verification."),
			_("Fill everything here, Save and Test."),
		]
