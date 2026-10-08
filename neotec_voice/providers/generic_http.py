import json

import frappe
from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.routing import dial_string
from neotec_voice.core.util import dig, loads
from neotec_voice.providers.base import F, SOFTPHONE_FIELDS, BaseProvider, ProviderError, WebhookResult, register

EXAMPLE_MAPPING = {
	"external_id": "call.id",
	"kind_field": "event",
	"kinds": {"ringing": "ringing", "answered": "answered", "hangup": "ended"},
	"status": "call.status",
	"direction": "call.direction",
	"from_number": "call.from",
	"to_number": "call.to",
	"agent_hint": "call.agent",
	"duration": "call.duration",
	"talk_time": "call.billsec",
	"recording_url": "call.recording",
	"local_ref": "call.nt",
}
EXAMPLE_STATUS = {"answered": "Completed", "no-answer": "No Answer", "busy": "Busy", "failed": "Failed"}
EXAMPLE_BODY = '{"from_extension": "{{ agent.identity }}", "to": "{{ to }}", "caller_id": "{{ caller_id }}", "reference": "{{ call_name }}", "callback": "{{ webhook_url }}"}'


@register
class GenericHttpProvider(BaseProvider):
	vendor = "Generic HTTP"
	slug = "generic_http"
	label = "Any PBX / Carrier (Open Connector)"
	category = "Open Connector"
	color = "#111827"
	sort_order = 90
	tagline = "Connect anything that speaks HTTP"
	description = (
		"Template-driven connector for any PBX, carrier or CCaaS: Jinja request template for click-to-call, JSON "
		"path mapping for inbound events, and Neotec routing answers in text or JSON."
	)
	capabilities = frozenset({"outbound", "inbound", "events", "softphone"})
	fields = [
		F("outbound_url", "Click-to-call URL", placeholder="https://pbx.example.sa/api/originate"),
		F("outbound_method", "HTTP method", "Select", default="POST", options="POST\nGET\nPUT"),
		F("outbound_headers", "Extra headers (JSON)", "Code", default='{"Content-Type": "application/json"}'),
		F("auth_header", "Authorization header value", "Password", secret=True, placeholder="Bearer …"),
		F("outbound_body", "Request body (Jinja)", "Code", default=EXAMPLE_BODY,
			description="Context: agent, to, caller_id, call_name, webhook_url"),
		F("response_id_path", "Response call-id path", default="id"),
		F("inbound_mapping", "Inbound event mapping (JSON)", "Code", default=json.dumps(EXAMPLE_MAPPING, indent=1)),
		F("status_map", "Status map (JSON)", "Code", default=json.dumps(EXAMPLE_STATUS, indent=1)),
		F("route_format", "Routing answer format", "Select", default="json", options="json\ntext"),
		F("agent_dial_format", "Agent dial format (text routing)", default="PJSIP/{identity}"),
		F("default_caller_id", "Default Caller ID"),
	] + SOFTPHONE_FIELDS

	def make_call(self, agent, to, caller_id, call_name):
		if not self.cfg("outbound_url"):
			raise ProviderError(_("Set the click-to-call URL."))
		ctx = {"agent": agent, "to": to, "caller_id": caller_id or self.cfg("default_caller_id"), "call_name": call_name,
			"webhook_url": self.webhook_url("event", nt=call_name)}
		body = frappe.render_template(self.cfg("outbound_body") or "{}", ctx)
		headers = loads(self.cfg("outbound_headers"), {}) or {}
		if self.secret("auth_header"):
			headers["Authorization"] = self.secret("auth_header")
		method = self.cfg("outbound_method", "POST")
		kw = {"params": loads(body, {})} if method == "GET" else {"data": body.encode()}
		res = self.http(method, frappe.render_template(self.cfg("outbound_url"), ctx), headers=headers, **kw)
		return str(dig(res, self.cfg("response_id_path", "id")) or call_name)

	def test_connection(self):
		loads(self.cfg("inbound_mapping"), None) or frappe.throw(_("Inbound mapping is not valid JSON"))
		return {"ok": True, "message": _("Templates are valid JSON. Place a test call to verify end-to-end.")}

	def handle_webhook(self, phase, p, request):
		if phase == "route":
			target = self.route_inbound(p.get("to"), p.get("from"))
			ev = CallEvent(external_id=p.get("id") or p.get("call_id"), kind="ringing", direction="Inbound",
				from_number=p.get("from"), to_number=p.get("to"), ring_agents=target.users, queue=target.queue,
				route=target.route, raw=p)
			if self.cfg("route_format") == "text":
				body = dial_string(target, self.cfg("agent_dial_format", "PJSIP/{identity}"), "{number}", "&")
				return WebhookResult([ev] if ev.external_id else [], body)
			body = {"kind": target.kind, "number": target.number, "timeout": target.timeout, "message": target.message,
				"agents": [{"user": a.user, "identity": a.identity, "mobile": a.mobile_no, "device": a.device}
					for a in target.ring_list()]}
			return WebhookResult([ev] if ev.external_id else [], json.dumps(body), "application/json")
		m = loads(self.cfg("inbound_mapping"), EXAMPLE_MAPPING) or EXAMPLE_MAPPING
		smap = loads(self.cfg("status_map"), {}) or {}
		raw_kind = str(dig(p, m.get("kind_field")) or "").lower()
		kind = (m.get("kinds") or {}).get(raw_kind, "status")
		raw_status = str(dig(p, m.get("status")) or "").lower()
		status = smap.get(raw_status) or (smap.get(raw_kind) if kind == "ended" else None)
		direction = str(dig(p, m.get("direction")) or "").title() or None
		ev = CallEvent(
			external_id=dig(p, m.get("external_id")), local_ref=dig(p, m.get("local_ref")) or p.get("nt"), kind=kind,
			status=status, direction=direction if direction in ("Inbound", "Outbound", "Internal") else None,
			from_number=dig(p, m.get("from_number")), to_number=dig(p, m.get("to_number")),
			agent_hint=dig(p, m.get("agent_hint")), duration=dig(p, m.get("duration")),
			talk_time=dig(p, m.get("talk_time")), recording_url=dig(p, m.get("recording_url")), raw=p)
		return WebhookResult([ev], json.dumps({"ok": True}), "application/json")

	def webhooks(self):
		return [(_("Event webhook (POST JSON or form)"), "event", ""), (_("Routing decision (GET/POST)"), "route", "")]

	def setup_steps(self):
		return [
			_("Click-to-call: describe your PBX/carrier originate API with URL, method, headers and a Jinja body."),
			_("Events: have the PBX POST JSON to the event webhook; map fields with dotted paths (e.g. data.call.id)."),
			_("Routing: let the PBX ask Neotec who to ring via the routing URL (text dial-string or JSON)."),
			_("Echo the 'nt' value Neotec sends on click-to-call back in events so calls link to the right record."),
		]
