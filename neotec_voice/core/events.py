"""Normalised call events and the single ingest pipeline every vendor feeds.

Vendors differ in everything: ids, status words, leg models, timestamps.
Each provider translates its payload into ``CallEvent`` objects; from here on
Neotec owns the call lifecycle — idempotency, status monotonicity, agent
presence, screen-pop, rating and post-call intelligence."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe.utils import cint, flt, now_datetime, time_diff_in_seconds

from neotec_voice.core import agents as agent_svc
from neotec_voice.core.contacts import lookup
from neotec_voice.core.phone import find_did, to_e164
from neotec_voice.core.util import loads, to_dt

CALL = "Voice Call"
TERMINAL = {"Completed", "Busy", "No Answer", "Missed", "Voicemail", "Failed", "Canceled"}
RANK = {"Initiated": 0, "Queued": 1, "Ringing": 2, "In Progress": 3, "On Hold": 3}
KIND_STATUS = {
	"initiated": "Initiated",
	"queued": "Queued",
	"ringing": "Ringing",
	"answered": "In Progress",
	"ended": "Completed",
	"voicemail": "Voicemail",
}


@dataclass
class CallEvent:
	external_id: str | None = None
	kind: str = "status"  # initiated|queued|ringing|answered|ended|recording|voicemail|status
	status: str | None = None
	direction: str | None = None  # Inbound | Outbound | Internal
	from_number: str | None = None
	to_number: str | None = None
	agent_hint: str | None = None
	duration: Any = None
	talk_time: Any = None
	started_at: Any = None
	answered_at: Any = None
	ended_at: Any = None
	recording_url: str | None = None
	hangup_cause: str | None = None
	vendor_cost: Any = None
	vendor_ref: str | None = None
	local_ref: str | None = None  # our Voice Call name, echoed back by the vendor
	ring_agents: list = field(default_factory=list)
	queue: str | None = None
	route: str | None = None
	raw: dict = field(default_factory=dict)


def _dedupe_key(account_name, external_id):
	return f"{account_name}::{external_id}"[:140]


def _event_hash(account_name, ev):
	body = json.dumps(ev.raw or {}, sort_keys=True, default=str)
	src = f"{account_name}|{ev.external_id}|{ev.local_ref}|{ev.kind}|{ev.status}|{body}"
	return hashlib.sha1(src.encode()).hexdigest()


def _find(account, ev):
	if ev.local_ref and frappe.db.exists(CALL, ev.local_ref):
		return frappe.get_doc(CALL, ev.local_ref)
	if ev.external_id:
		name = frappe.db.get_value(CALL, {"dedupe_key": _dedupe_key(account.name, ev.external_id)})
		if name:
			return frappe.get_doc(CALL, name)
	return None


def ingest(account, ev):
	"""Apply one normalised event. Idempotent: replays are no-ops."""
	if not (ev.external_id or ev.local_ref):
		return None
	digest = _event_hash(account.name, ev)
	if frappe.db.exists("Voice Call Event", {"event_hash": digest}):
		return None

	for attempt in (1, 2):
		call = _find(account, ev)
		is_new = call is None
		if is_new:
			call = frappe.new_doc(CALL)
			call.provider_account = account.name
			call.vendor = account.vendor
			call.direction = ev.direction or "Inbound"
			call.status = "Initiated"
		before = frappe._dict(status=call.status, answered=bool(call.answered_at), agent=call.agent,
			recording=call.recording_url)
		_apply(call, account, ev, is_new)
		frappe.db.savepoint("ntv_ingest")
		try:
			call.flags.ignore_permissions = True
			call.flags.ignore_links = True
			call.insert() if is_new else call.save()
			break
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
			# Two webhooks for the same new call raced; the other one won.
			frappe.db.rollback(save_point="ntv_ingest")
			if attempt == 2:
				raise

	_log(call, account, ev, digest)
	_side_effects(call, account, ev, is_new, before)
	return call


def _apply(call, account, ev, is_new):
	now = now_datetime()
	if ev.external_id and (not call.external_id or call.external_id == call.name):
		call.external_id = ev.external_id
		call.dedupe_key = _dedupe_key(account.name, ev.external_id)
	if ev.direction and is_new:
		call.direction = ev.direction
	if ev.from_number and not call.from_number:
		call.from_number = to_e164(ev.from_number)
	if ev.to_number and not call.to_number:
		call.to_number = to_e164(ev.to_number)
	if ev.vendor_ref and not call.vendor_ref:
		call.vendor_ref = ev.vendor_ref
	if ev.queue and not call.queue:
		call.queue = ev.queue
	if ev.route and not call.route:
		call.route = ev.route
	if ev.ring_agents:
		rang = set(loads(call.rang_agents, []) or [])
		rang.update(ev.ring_agents)
		call.rang_agents = json.dumps(sorted(rang))
	if not call.agent and ev.agent_hint:
		call.agent = agent_svc.resolve_agent(account.name, ev.agent_hint)

	new = ev.status or KIND_STATUS.get(ev.kind)
	if new and call.status not in TERMINAL:
		if new in TERMINAL or RANK.get(new, 0) >= RANK.get(call.status, 0):
			call.status = new

	call.started_at = call.started_at or to_dt(ev.started_at) or now
	if call.status == "In Progress" or ev.answered_at:
		call.answered_at = call.answered_at or to_dt(ev.answered_at) or now
	if ev.duration not in (None, ""):
		call.duration = cint(ev.duration)
	if ev.talk_time not in (None, ""):
		call.talk_time = cint(ev.talk_time)
	if ev.recording_url:
		call.recording_url = ev.recording_url
	if ev.hangup_cause:
		call.hangup_cause = str(ev.hangup_cause)[:140]
	if ev.vendor_cost not in (None, ""):
		call.buy_cost = abs(flt(ev.vendor_cost))
		call.cost_from_vendor = 1

	if not call.did:
		call.did = find_did(call.to_number if call.direction == "Inbound" else call.from_number)
	if not call.party and (is_new or ev.kind in ("ringing", "answered")):
		_link_party(call)
	if call.status in TERMINAL:
		call.ended_at = call.ended_at or to_dt(ev.ended_at) or now
		_finalise(call, account)


def _link_party(call):
	other = call.from_number if call.direction == "Inbound" else call.to_number
	hit = lookup(other) if other else {}
	if not hit:
		return
	call.party_type, call.party, call.party_name = hit["party_type"], hit["party"], hit["party_name"]
	existing = {(l.link_doctype, l.link_name) for l in call.get("links") or []}
	for dt, dn in [(hit["party_type"], hit["party"])] + [(l["link_doctype"], l["link_name"]) for l in hit["links"]]:
		if (dt, dn) not in existing:
			call.append("links", {"link_doctype": dt, "link_name": dn})
			existing.add((dt, dn))


def _finalise(call, account):
	if not call.duration and call.started_at and call.ended_at:
		call.duration = max(0, int(time_diff_in_seconds(call.ended_at, call.started_at)))
	if not call.talk_time and call.answered_at and call.ended_at:
		call.talk_time = max(0, int(time_diff_in_seconds(call.ended_at, call.answered_at)))
	if call.talk_time and not call.answered_at and call.ended_at:
		from frappe.utils import add_to_date

		call.answered_at = add_to_date(call.ended_at, seconds=-cint(call.talk_time))
	if call.direction == "Inbound" and not call.answered_at and call.status in ("No Answer", "Canceled", "Busy"):
		call.status = "Missed"
	if call.answered_at and call.started_at:
		call.wait_time = max(0, int(time_diff_in_seconds(call.answered_at, call.started_at)))

	from neotec_voice.core.rating import rate

	billable = cint(call.talk_time) or (cint(call.duration) if call.answered_at else 0)
	if not cint(call.cost_from_vendor) and account.buy_rate_plan and call.direction == "Outbound":
		call.buy_cost = rate(account.buy_rate_plan, call.to_number, billable)
	if call.did:
		num = frappe.db.get_value("Voice Number", call.did,
			["sell_rate_plan", "billing_party_type", "billing_party"], as_dict=True)
		if num:
			call.billing_party_type = call.billing_party_type or num.billing_party_type
			call.billing_party = call.billing_party or num.billing_party
			if num.sell_rate_plan:
				dest = call.to_number if call.direction == "Outbound" else call.from_number
				call.sell_amount = rate(num.sell_rate_plan, dest, billable)


def _log(call, account, ev, digest):
	try:
		frappe.get_doc(
			{
				"doctype": "Voice Call Event",
				"call": call.name,
				"provider_account": account.name,
				"kind": ev.kind,
				"status": ev.status or KIND_STATUS.get(ev.kind),
				"event_hash": digest,
				"payload": json.dumps(ev.raw or {}, default=str, indent=1)[:60000],
			}
		).insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		pass


def _pop_payload(call, event):
	other = call.from_number if call.direction == "Inbound" else call.to_number
	return {
		"event": event,
		"call": call.name,
		"direction": call.direction,
		"status": call.status,
		"number": other,
		"did": call.did,
		"queue": call.queue,
		"party_type": call.party_type,
		"party": call.party,
		"party_name": call.party_name,
		"agent": call.agent,
		"answered": bool(call.answered_at),
	}


def notify(users, call, event):
	data = _pop_payload(call, event)
	for user in {u for u in users if u}:
		frappe.publish_realtime("neotec_voice_call", data, user=user, after_commit=True)


def _side_effects(call, account, ev, is_new, before):
	rang = set(loads(call.rang_agents, []) or [])
	status_changed = call.status != before.status

	if status_changed and call.status == "Ringing" and call.direction == "Inbound":
		notify(rang or {call.agent}, call, "ringing")

	if call.status == "In Progress" and not before.answered:
		if call.agent:
			agent_svc.set_status(call.agent, "On Call")
			notify({call.agent}, call, "answered")
		notify(rang - {call.agent}, call, "taken")

	if status_changed and call.status in TERMINAL and before.status not in TERMINAL:
		if call.agent:
			if frappe.db.get_value("Voice Agent", call.agent, "status") == "On Call":
				agent_svc.set_status(call.agent, "Wrap Up")
			notify({call.agent}, call, "ended")
		notify(rang - {call.agent}, call, "taken")
		_enqueue_post_call(call)
	elif ev.recording_url and call.status in TERMINAL and ev.recording_url != before.recording:
		_enqueue_post_call(call)

	if status_changed or is_new:
		frappe.publish_realtime("neotec_voice_board", {"call": call.name, "status": call.status}, after_commit=True)
	frappe.db.set_value("Voice Provider Account", account.name, "last_webhook_on", now_datetime(),
		update_modified=False)


def _enqueue_post_call(call):
	from neotec_voice.core.util import settings

	s = settings()
	if call.recording_url and (cint(s.store_recordings_locally) or cint(s.ai_enabled)):
		frappe.enqueue(
			"neotec_voice.core.intelligence.post_call",
			queue="long",
			call_name=call.name,
			enqueue_after_commit=True,
			job_id=f"ntv-post-{call.name}",
			deduplicate=True,
		)
