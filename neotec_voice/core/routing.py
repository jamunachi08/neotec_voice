"""The Neotec routing brain. Vendors ask *where should this call go*; we
answer from DIDs, time-based routes, queues and live agent presence. The
same decision is rendered as TwiML, NCCO, Plivo XML, an Asterisk dial string
or a FreeSWITCH bridge string by each provider."""

from dataclasses import dataclass, field

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from neotec_voice.core.agents import available_agents
from neotec_voice.core.phone import digits, find_did, to_e164
from neotec_voice.core.util import as_time, setting, split_csv

MAX_DEPTH = 4


@dataclass
class Target:
	kind: str = "agents"  # agents | number | voicemail | message | hangup
	agents: list = field(default_factory=list)
	simultaneous: bool = True
	number: str | None = None
	message: str | None = None
	message_ar: str | None = None
	queue: str | None = None
	did: str | None = None
	route: str | None = None
	timeout: int = 25
	record: bool = False

	@property
	def users(self):
		return [a.user for a in self.agents]

	def ring_list(self):
		return self.agents if self.simultaneous else self.agents[:1]


def resolve_inbound(account, to_number, from_number):
	did_name = find_did(to_number)
	did = frappe.db.get_value("Voice Number", did_name, ["name", "default_target_type", "default_queue",
		"default_agent", "provider_account"], as_dict=True) if did_name else None
	route = match_route(account.name, did_name, from_number)
	if route:
		target = _from_route(route, account)
	elif did and did.default_target_type:
		target = _from_spec(did.default_target_type, account, queue=did.default_queue, agent=did.default_agent)
	else:
		target = Target(agents=available_agents(account.name))
	target.did = did_name
	target.record = target.record or _record_flag(account)
	return _ensure_reachable(target, account)


def _record_flag(account):
	from neotec_voice.core.util import loads

	return bool(cint((loads(account.config, {}) or {}).get("record_calls")))


def match_route(account_name, did_name, caller):
	now = now_datetime()
	day = now.strftime("%a")
	clock = now.time()
	caller_digits = digits(to_e164(caller))
	routes = frappe.get_all(
		"Voice Route",
		filters={"enabled": 1},
		fields=["name", "did", "provider_account", "caller_prefix", "days", "from_time", "to_time"],
		order_by="priority asc, creation asc",
	)
	for r in routes:
		if r.did and r.did != did_name:
			continue
		if r.provider_account and r.provider_account != account_name:
			continue
		if r.caller_prefix and not any(caller_digits.startswith(digits(p)) for p in split_csv(r.caller_prefix)):
			continue
		if r.days and day not in [d[:3].title() for d in split_csv(r.days)]:
			continue
		start, end = as_time(r.from_time), as_time(r.to_time)
		if start and end:
			inside = start <= clock <= end if start <= end else (clock >= start or clock <= end)
			if not inside:
				continue
		return frappe.get_doc("Voice Route", r.name)
	return None


def _from_route(route, account):
	target = _from_spec(
		route.target_type,
		account,
		queue=route.target_queue,
		agent=route.target_agent,
		number=route.target_number,
		message=route.message,
		message_ar=route.message_ar,
	)
	target.route = route.name
	return target


def _from_spec(kind, account, queue=None, agent=None, number=None, message=None, message_ar=None, depth=0):
	if depth > MAX_DEPTH:
		return voicemail_target()
	if kind == "Queue" and queue:
		return queue_target(queue, account, depth)
	if kind == "Agent" and agent:
		agents = available_agents(account.name, [agent])
		return Target(agents=agents) if agents else voicemail_target()
	if kind == "Number" and number:
		return Target(kind="number", number=to_e164(number))
	if kind == "Message":
		return Target(kind="message", message=message, message_ar=message_ar)
	if kind == "Voicemail":
		return voicemail_target(message, message_ar)
	return Target(agents=available_agents(account.name))


def voicemail_target(message=None, message_ar=None):
	return Target(
		kind="voicemail",
		message=message or setting("voicemail_message", "Please leave a message after the tone."),
		message_ar=message_ar or setting("voicemail_message_ar"),
	)


def queue_target(queue_name, account, depth=0):
	q = frappe.get_doc("Voice Queue", queue_name)
	members = [m for m in q.members if cint(m.enabled)]
	members.sort(key=lambda m: cint(m.priority))
	order = [m.agent for m in members]
	agents = {a.user: a for a in available_agents(account.name, order)}
	ranked = [agents[u] for u in order if u in agents]
	strategy = q.strategy or "Ring All"
	if strategy == "Round Robin" and ranked:
		start = (cint(q.last_index) + 1) % len(ranked)
		ranked = ranked[start:] + ranked[:start]
		frappe.db.set_value("Voice Queue", q.name, "last_index", start, update_modified=False)
	elif strategy == "Longest Idle":
		ranked.sort(key=lambda a: get_datetime(a.last_call_end) if a.last_call_end else get_datetime("2000-01-01"))
	elif strategy == "Fewest Calls":
		counts = _calls_today([a.user for a in ranked])
		ranked.sort(key=lambda a: counts.get(a.user, 0))
	if not ranked:
		return _overflow(q, account, depth)
	return Target(
		agents=ranked,
		simultaneous=strategy == "Ring All",
		queue=q.name,
		timeout=cint(q.ring_timeout) or 25,
		message=q.welcome_message,
		message_ar=q.welcome_message_ar,
	)


def _overflow(q, account, depth):
	kind = q.overflow_type or "Voicemail"
	if kind == "None":
		return Target(kind="hangup", message=q.unavailable_message)
	return _from_spec(kind, account, queue=q.overflow_queue, agent=q.overflow_agent,
		number=q.overflow_number, depth=depth + 1)


def _calls_today(users):
	if not users:
		return {}
	rows = frappe.db.sql(
		"""select agent, count(*) as n from `tabVoice Call`
		where agent in %(users)s and date(creation) = curdate() group by agent""",
		{"users": tuple(users)},
		as_dict=True,
	)
	return {r.agent: r.n for r in rows}


def _ensure_reachable(target, account):
	if target.kind == "agents" and not target.agents:
		return voicemail_target()
	return target


def dial_string(target, fmt_agent, fmt_number, joiner="&"):
	"""Render a Target for PBX dialplans: Asterisk ('PJSIP/1001&PJSIP/1002')
	or FreeSWITCH ('user/1001@dom,user/1002@dom')."""
	if target.kind == "agents":
		return joiner.join(fmt_agent.format(identity=a.identity, mobile=a.mobile_no or "") for a in target.ring_list())
	if target.kind == "number" and target.number:
		return fmt_number.format(number=target.number, digits=digits(target.number))
	return ""
