"""Neotec Voice public API v1 — vendor-neutral.

Any Frappe app (ERPNext, CRM, Helpdesk, HRMS, custom) integrates once with
these methods and works with every carrier and PBX Neotec supports."""

import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, getdate, nowdate

from neotec_voice.core import agents as agent_svc
from neotec_voice.core import otp as otp_svc
from neotec_voice.core import sms as sms_svc
from neotec_voice.core.contacts import lookup as _lookup
from neotec_voice.core.outbound import new_call, place_call
from neotec_voice.providers import get_provider


def _agent_or_throw():
	agent = agent_svc.get_agent()
	if not agent:
		frappe.throw(_("You are not configured as a Voice Agent."), frappe.PermissionError)
	return agent


@frappe.whitelist()
def call(to, link_doctype=None, link_name=None, account=None):
	"""Click-to-call: rings the agent's device first, then the destination."""
	_agent_or_throw()
	if link_doctype and link_name:
		frappe.has_permission(link_doctype, "read", link_name, throw=True)
	c = place_call(to, frappe.session.user, link_doctype, link_name, account)
	return {"call": c.name, "account": c.provider_account, "status": c.status}


@frappe.whitelist()
def prepare_call(to, link_doctype=None, link_name=None):
	"""For browser-softphone calls: create the record first, then the
	softphone passes the returned id to the PBX (SIP header X-NT-Call)."""
	agent = _agent_or_throw()
	if link_doctype and link_name:
		frappe.has_permission(link_doctype, "read", link_name, throw=True)
	c = new_call(to, agent.name, link_doctype, link_name, agent.default_account,
		frappe.db.get_value("Voice Provider Account", agent.default_account, "vendor") if agent.default_account else None)
	return {"call": c.name}


@frappe.whitelist()
def hangup(call):
	doc = frappe.get_doc("Voice Call", call)
	if doc.agent != frappe.session.user and "Voice Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	get_provider(doc.provider_account).hangup(doc)
	return {"ok": True}


@frappe.whitelist()
def me():
	agent = agent_svc.get_agent()
	if not agent:
		return None
	return {"user": agent.name, "status": agent.status, "device": agent.device, "extension": agent.extension,
		"default_account": agent.default_account}


@frappe.whitelist()
def set_status(status):
	_agent_or_throw()
	agent_svc.set_status(frappe.session.user, status)
	return me()


@frappe.whitelist()
def heartbeat():
	if agent_svc.get_agent():
		agent_svc.heartbeat(frappe.session.user)
	return {"ok": True}


@frappe.whitelist()
def lookup(number):
	if not frappe.has_permission("Voice Call", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return _lookup(number)


@frappe.whitelist()
def save_wrap_up(call, disposition=None, notes=None, follow_up_on=None):
	doc = frappe.get_doc("Voice Call", call)
	if doc.agent and doc.agent != frappe.session.user and "Voice Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	doc.disposition = disposition or doc.disposition
	doc.notes = notes or doc.notes
	doc.follow_up_on = follow_up_on or doc.follow_up_on
	doc.save(ignore_permissions=True)
	if follow_up_on and doc.party_type and doc.party:
		frappe.get_doc({
			"doctype": "ToDo", "allocated_to": frappe.session.user, "date": follow_up_on,
			"reference_type": doc.party_type, "reference_name": doc.party,
			"description": _("Follow up on call {0}: {1}").format(doc.name, notes or disposition or ""),
		}).insert(ignore_permissions=True)
	if frappe.db.get_value("Voice Agent", frappe.session.user, "status") == "Wrap Up":
		agent_svc.set_status(frappe.session.user, "Available")
	return {"ok": True}


@frappe.whitelist()
def get_call(call):
	doc = frappe.get_doc("Voice Call", call)
	doc.check_permission("read")
	return {k: doc.get(k) for k in ("name", "direction", "status", "from_number", "to_number", "party_type", "party",
		"party_name", "agent", "talk_time", "disposition", "notes", "summary")}


@frappe.whitelist()
def recent_calls(link_doctype, link_name, limit=20):
	frappe.has_permission(link_doctype, "read", link_name, throw=True)
	return frappe.db.sql(
		"""select c.name, c.direction, c.status, c.from_number, c.to_number, c.talk_time, c.agent, c.creation
		from `tabVoice Call` c inner join `tabDynamic Link` l on l.parent=c.name and l.parenttype='Voice Call'
		where l.link_doctype=%s and l.link_name=%s order by c.creation desc limit %s""",
		(link_doctype, link_name, min(cint(limit) or 20, 100)), as_dict=True)


@frappe.whitelist()
def softphone_config():
	"""WebRTC config for the current agent only (credentials are theirs)."""
	agent = agent_svc.get_agent()
	if not agent or agent.device != "Softphone" or not agent.default_account:
		return None
	acc = frappe.get_doc("Voice Provider Account", agent.default_account)
	if not acc.enabled:
		return None
	provider = get_provider(acc)
	if "softphone" not in provider.capabilities:
		return None
	cfg = provider.webrtc_config(agent)
	if cfg and cfg.get("kind") == "sip" and not cfg.get("lib_url"):
		cfg["lib_url"] = frappe.db.get_single_value("Voice Settings", "jssip_url")
	return cfg


@frappe.whitelist()
def send_sms(to, message, account=None, reference_doctype=None, reference_name=None):
	if not frappe.has_permission("Voice Message", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return sms_svc.send(to, message, account=account, reference_doctype=reference_doctype, reference_name=reference_name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=600)
def otp_send(recipient, channel="SMS", purpose="Verification"):
	return otp_svc.generate(recipient, channel, purpose)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=600)
def otp_verify(recipient, code, channel="SMS", purpose="Verification"):
	return otp_svc.verify(recipient, code, channel, purpose)


@frappe.whitelist()
def live():
	"""Wallboard: agents + active calls."""
	if not frappe.has_permission("Voice Call", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	agents = frappe.get_all("Voice Agent", filters={"enabled": 1}, fields=["name", "full_name", "status",
		"status_since", "device", "extension"], order_by="full_name asc")
	calls = frappe.get_all("Voice Call", filters={"status": ["in", ["Initiated", "Queued", "Ringing", "In Progress", "On Hold"]],
		"creation": [">", frappe.utils.add_to_date(None, hours=-4)]},
		fields=["name", "direction", "status", "from_number", "to_number", "agent", "party_name", "queue", "creation",
			"answered_at"], order_by="creation desc", limit=100)
	return {"agents": agents, "calls": calls}


@frappe.whitelist()
def kpis(date=None):
	if not frappe.has_permission("Voice Call", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	d = getdate(date or nowdate())
	row = frappe.db.sql(
		"""select count(*) total,
			sum(direction='Inbound') inbound, sum(direction='Outbound') outbound,
			sum(answered_at is not null) answered, sum(status='Missed') missed,
			avg(case when talk_time > 0 then talk_time end) aht,
			avg(case when direction='Inbound' and answered_at is not null then wait_time end) asa,
			sum(direction='Inbound' and answered_at is not null and wait_time <= 20) within_sla,
			sum(direction='Inbound') inbound_all,
			sum(buy_cost) cost, sum(sell_amount) revenue
		from `tabVoice Call` where date(creation)=%s""", d, as_dict=True)[0]
	inbound = cint(row.inbound_all)
	row["sla_pct"] = round(100.0 * cint(row.within_sla) / inbound, 1) if inbound else None
	return row
