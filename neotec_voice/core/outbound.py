"""Outbound placement with least-cost routing and automatic carrier failover."""

import json

import frappe
from frappe import _
from frappe.utils import cint

from neotec_voice.core import agents as agent_svc
from neotec_voice.core.contacts import lookup
from neotec_voice.core.phone import digits, find_did, to_e164
from neotec_voice.core.util import split_csv


def _route_for(dest):
	d = digits(dest)
	for r in frappe.get_all(
		"Voice Outbound Route",
		filters={"enabled": 1},
		fields=["name", "prefixes", "strip_digits", "prepend"],
		order_by="priority asc, creation asc",
	):
		prefixes = split_csv(r.prefixes) or ["*"]
		if any(p == "*" or d.startswith(digits(p)) for p in prefixes):
			return r
	return None


def carriers_for(dest, agent=None):
	route = _route_for(dest)
	names = []
	if route:
		rows = frappe.get_all(
			"Voice Outbound Carrier",
			filters={"parent": route.name, "parenttype": "Voice Outbound Route"},
			fields=["provider_account"],
			order_by="priority asc, idx asc",
		)
		names = [r.provider_account for r in rows]
	if not names and agent and agent.get("default_account"):
		names = [agent.default_account]
	if not names:
		names = frappe.get_all(
			"Voice Provider Account", filters={"enabled": 1}, order_by="is_default desc, creation asc", pluck="name"
		)
	return route, names


def transform(dest, route):
	if not route:
		return dest
	d = to_e164(dest)
	strip = cint(route.strip_digits)
	if strip:
		d = d[strip:] if not d.startswith("+") else "+" + d[1 + strip:]
	if route.prepend:
		d = route.prepend + d
	return d


def caller_id_for(agent, account, provider):
	"""Agent's own DID on that account, else the account default."""
	rows = frappe.get_all(
		"Voice Number",
		filters={"provider_account": account.name, "default_agent": agent.name, "enabled": 1},
		pluck="name",
		limit=1,
	)
	return rows[0] if rows else provider.cfg("default_caller_id")


def new_call(to, agent_user, link_doctype=None, link_name=None, account=None, vendor=None):
	call = frappe.new_doc("Voice Call")
	call.direction = "Outbound"
	call.status = "Initiated"
	call.to_number = to_e164(to)
	call.agent = agent_user
	call.provider_account = account
	call.vendor = vendor
	if link_doctype and link_name and frappe.db.exists(link_doctype, link_name):
		call.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	hit = lookup(to)
	if hit:
		call.party_type, call.party, call.party_name = hit["party_type"], hit["party"], hit["party_name"]
		if not (link_doctype == hit["party_type"] and link_name == hit["party"]):
			call.append("links", {"link_doctype": hit["party_type"], "link_name": hit["party"]})
	call.flags.ignore_permissions = True
	call.insert()
	return call


def place_call(to, agent_user=None, link_doctype=None, link_name=None, account=None):
	from neotec_voice.providers import get_provider

	agent_user = agent_user or frappe.session.user
	agent = agent_svc.get_agent(agent_user)
	if not agent:
		frappe.throw(_("You are not configured as a Voice Agent."), title=_("Voice"))
	dest = to_e164(to)
	if not dest:
		frappe.throw(_("Enter a number to call."))
	route, candidates = (None, [account]) if account else carriers_for(dest, agent)
	errors = []
	for acc_name in candidates:
		acc = frappe.get_doc("Voice Provider Account", acc_name)
		if not acc.enabled:
			continue
		provider = get_provider(acc)
		if "outbound" not in provider.capabilities:
			continue
		dialled = transform(dest, route)
		cid = caller_id_for(agent, acc, provider)
		call = new_call(dest, agent.name, link_doctype, link_name, acc.name, acc.vendor)
		call.from_number = to_e164(cid) if cid else None
		try:
			ext = provider.make_call(agent_svc.payload(agent, acc.name), dialled, cid, call.name)
			if ext:
				call.external_id = str(ext)
				call.dedupe_key = f"{acc.name}::{ext}"[:140]
			call.attempt_log = json.dumps(errors) if errors else None
			call.save(ignore_permissions=True)
			return call
		except Exception as e:
			call.status = "Failed"
			call.hangup_cause = str(e)[:140]
			call.save(ignore_permissions=True)
			errors.append({"account": acc.name, "error": str(e)[:300]})
			frappe.log_error(title=f"Neotec Voice: call via {acc.name} failed", message=frappe.get_traceback())
	frappe.throw(
		_("No carrier could place the call.") + "<br>" + "<br>".join(f"<b>{e['account']}</b>: {frappe.utils.escape_html(e['error'])}" for e in errors)
		if errors else _("No enabled provider account supports outbound calls."),
		title=_("Call failed"),
	)
