"""Agent presence, identity resolution and selection helpers."""

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, time_diff_in_seconds

from neotec_voice.core.phone import match_key, strip_uri, to_e164
from neotec_voice.core.util import setting

AGENT = "Voice Agent"
STATUSES = ("Available", "On Call", "Wrap Up", "Away", "Offline")


def get_agent(user=None):
	user = user or frappe.session.user
	if not user or user == "Guest" or not frappe.db.exists(AGENT, user):
		return None
	return frappe.get_doc(AGENT, user)


def identity_for(agent, account_name):
	"""The vendor-side identity (extension, SIP user, client id) of an agent
	on a given provider account."""
	for row in agent.get("identities") or []:
		if row.provider_account == account_name and row.identity:
			return row.identity
	return agent.get("extension") or agent.get("sip_username") or agent.get("name")


def payload(agent, account_name=None):
	"""Plain dict handed to providers. Never carries secrets."""
	return frappe._dict(
		user=agent.name,
		full_name=agent.get("full_name") or agent.name,
		extension=agent.get("extension"),
		mobile_no=to_e164(agent.get("mobile_no")) if agent.get("mobile_no") else None,
		device=agent.get("device") or "Softphone",
		identity=identity_for(agent, account_name) if account_name else agent.get("extension"),
		sip_username=agent.get("sip_username"),
	)


def resolve_agent(account_name, hint):
	"""Map whatever a vendor tells us (email, extension, client id, SIP URI,
	mobile) to a Voice Agent name."""
	if not hint:
		return None
	text = strip_uri(hint)
	if not text:
		return None
	if "@" in text and frappe.db.exists(AGENT, text):
		return text
	user = None
	if account_name:
		user = frappe.db.get_value(
			"Voice Agent Identity", {"provider_account": account_name, "identity": text}, "parent"
		)
	user = (
		user
		or frappe.db.get_value(AGENT, {"extension": text}, "name")
		or frappe.db.get_value(AGENT, {"sip_username": text}, "name")
	)
	if user:
		return user
	key = match_key(text)
	if len(key) >= 8:
		for row in frappe.get_all(AGENT, fields=["name", "mobile_no"], filters={"mobile_no": ["is", "set"]}):
			if match_key(row.mobile_no) == key:
				return row.name
	return None


def is_online(row):
	if (row.get("device") or "Softphone") != "Softphone":
		return True
	if not row.get("last_seen"):
		return False
	timeout = cint(setting("heartbeat_timeout", 150)) or 150
	return time_diff_in_seconds(now_datetime(), get_datetime(row.last_seen)) <= timeout


def available_agents(account_name, users=None):
	"""Available + online agents, enriched with their identity on the account."""
	filters = {"status": "Available", "enabled": 1}
	if users is not None:
		if not users:
			return []
		filters["name"] = ["in", list(users)]
	rows = frappe.get_all(
		AGENT,
		filters=filters,
		fields=["name", "full_name", "extension", "mobile_no", "device", "last_seen", "last_call_end", "sip_username"],
	)
	ids = {}
	if account_name:
		for r in frappe.get_all(
			"Voice Agent Identity", filters={"provider_account": account_name}, fields=["parent", "identity"]
		):
			ids[r.parent] = r.identity
	out = []
	for r in rows:
		if not is_online(r):
			continue
		out.append(
			frappe._dict(
				user=r.name,
				full_name=r.full_name or r.name,
				extension=r.extension,
				mobile_no=to_e164(r.mobile_no) if r.mobile_no else None,
				device=r.device or "Softphone",
				identity=ids.get(r.name) or r.extension or r.sip_username or r.name,
				last_call_end=r.last_call_end,
			)
		)
	return out


def set_status(user, status, publish=True):
	if not user or not frappe.db.exists(AGENT, user):
		return
	if status not in STATUSES:
		frappe.throw(frappe._("Invalid agent status {0}").format(status))
	values = {"status": status, "status_since": now_datetime()}
	if status == "Wrap Up":
		wrap = cint(setting("wrap_up_seconds", 30))
		if wrap <= 0:
			values["status"] = "Available"
		else:
			values["wrap_up_until"] = add_to_date(now_datetime(), seconds=wrap)
	if status in ("Wrap Up", "Available") and frappe.db.get_value(AGENT, user, "status") == "On Call":
		values["last_call_end"] = now_datetime()
	frappe.db.set_value(AGENT, user, values, update_modified=False)
	if publish:
		frappe.publish_realtime("neotec_voice_board", {"agent": user, "status": values["status"]}, after_commit=True)


def heartbeat(user):
	frappe.db.set_value(AGENT, user, "last_seen", now_datetime(), update_modified=False)


def release_wrap_up():
	"""Scheduler (every minute): return expired Wrap Up agents to Available."""
	for name in frappe.get_all(
		AGENT, filters={"status": "Wrap Up", "wrap_up_until": ["<=", now_datetime()]}, pluck="name"
	):
		set_status(name, "Available")
