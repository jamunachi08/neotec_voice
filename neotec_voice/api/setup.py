"""Backend for the Voice Hub and the per-vendor setup pages."""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

from neotec_voice.core.phone import to_e164
from neotec_voice.providers import catalogue, get_provider, provider_class

MASK = "••••••••"
ACC = "Voice Provider Account"


def _guard():
	frappe.only_for(("Voice Manager", "System Manager"))


@frappe.whitelist()
def hub():
	_guard()
	counts = {}
	for r in frappe.get_all(ACC, fields=["vendor", "enabled", "last_test_ok", "last_webhook_on"]):
		c = counts.setdefault(r.vendor, {"accounts": 0, "enabled": 0, "healthy": 0, "last_webhook_on": None})
		c["accounts"] += 1
		c["enabled"] += cint(r.enabled)
		c["healthy"] += cint(r.enabled and r.last_test_ok)
		if r.last_webhook_on and (not c["last_webhook_on"] or r.last_webhook_on > c["last_webhook_on"]):
			c["last_webhook_on"] = r.last_webhook_on
	vendors = catalogue()
	for v in vendors:
		v.update(counts.get(v["vendor"], {"accounts": 0, "enabled": 0, "healthy": 0, "last_webhook_on": None}))
	return {"vendors": vendors, "agents": frappe.db.count("Voice Agent", {"enabled": 1}),
		"numbers": frappe.db.count("Voice Number", {"enabled": 1})}


@frappe.whitelist()
def vendor(vendor):
	_guard()
	cls = provider_class(vendor)
	if not cls:
		frappe.throw(_("Unknown vendor {0}").format(vendor))
	accounts = frappe.get_all(ACC, filters={"vendor": vendor}, fields=["name", "enabled", "is_default", "last_test_ok",
		"last_test_on", "last_webhook_on"], order_by="creation asc")
	return {"meta": cls.meta(), "accounts": accounts}


@frappe.whitelist()
def account(name):
	_guard()
	acc = frappe.get_doc(ACC, name)
	provider = get_provider(acc)
	values = dict(provider.config)
	for f in provider.fields:
		if f["secret"]:
			values[f["fieldname"]] = MASK if provider.secret(f["fieldname"]) else ""
		elif f["fieldname"] not in values and f.get("default") is not None:
			values[f["fieldname"]] = f["default"]
	identities = frappe.get_all("Voice Agent Identity", filters={"provider_account": name},
		fields=["parent as agent", "identity"], order_by="parent asc")
	numbers = frappe.get_all("Voice Number", filters={"provider_account": name},
		fields=["name", "label", "enabled", "default_target_type", "default_queue", "default_agent"])
	events = frappe.get_all("Voice Call Event", filters={"provider_account": name},
		fields=["name", "call", "kind", "status", "creation"], order_by="creation desc", limit=15)
	stats = frappe.db.sql("""select count(*) calls, sum(answered_at is not null) answered, sum(status='Failed') failed
		from `tabVoice Call` where provider_account=%s and creation >= %s""", (name, add_days(now_datetime(), -7)), as_dict=True)[0]

	try:
		snippets = provider.snippets()
	except Exception as e:
		snippets = [{"title": _("Could not render snippets"), "language": "text", "filename": "error.txt", "content": str(e)}]

	return {
		"name": acc.name,
		"vendor": acc.vendor,
		"enabled": acc.enabled,
		"is_default": acc.is_default,
		"buy_rate_plan": acc.buy_rate_plan,
		"last_test_ok": acc.last_test_ok,
		"last_test_on": acc.last_test_on,
		"last_test_message": acc.last_test_message,
		"last_webhook_on": acc.last_webhook_on,
		"values": values,
		"webhooks": [{"label": l, "url": provider.webhook_url(ph), "note": n} for (l, ph, n) in provider.webhooks()],
		"steps": provider.setup_steps(),
		"snippets": snippets,
		"identities": identities,
		"agents": frappe.get_all("Voice Agent", filters={"enabled": 1}, fields=["name", "full_name", "extension"]),
		"numbers": numbers,
		"events": events,
		"stats": stats,
	}


@frappe.whitelist()
def save(vendor, values, name=None, account_name=None, enabled=1, is_default=0, buy_rate_plan=None):
	_guard()
	cls = provider_class(vendor)
	if not cls:
		frappe.throw(_("Unknown vendor {0}").format(vendor))
	values = json.loads(values) if isinstance(values, str) else (values or {})
	if name:
		acc = frappe.get_doc(ACC, name)
	else:
		if not account_name:
			frappe.throw(_("Account name is required."))
		acc = frappe.new_doc(ACC)
		acc.account_name = account_name.strip()
		acc.vendor = vendor
	config = json.loads(acc.config or "{}") if acc.config else {}
	secrets = set(cls.secret_fields())
	secret_updates = {}
	for f in cls.fields:
		key = f["fieldname"]
		if key not in values:
			continue
		val = values.get(key)
		if key in secrets:
			if val != MASK:
				secret_updates[key] = val or ""
		else:
			config[key] = val
	acc.config = json.dumps(config, indent=1, sort_keys=True)
	acc.enabled = cint(enabled)
	acc.is_default = cint(is_default)
	acc.buy_rate_plan = buy_rate_plan or None
	acc.save()

	from frappe.utils.password import remove_encrypted_password, set_encrypted_password

	from neotec_voice.providers.base import secret_fieldname

	for key, val in secret_updates.items():
		if val:
			set_encrypted_password(ACC, acc.name, val, secret_fieldname(key))
		else:
			remove_encrypted_password(ACC, acc.name, secret_fieldname(key))
	return acc.name


@frappe.whitelist()
def test(name):
	_guard()
	acc = frappe.get_doc(ACC, name)
	try:
		res = get_provider(acc).test_connection() or {"ok": True}
	except Exception as e:
		res = {"ok": False, "message": str(e)[:500]}
	acc.db_set({"last_test_ok": 1 if res.get("ok") else 0, "last_test_on": now_datetime(),
		"last_test_message": (res.get("message") or "")[:500]})
	return res


@frappe.whitelist()
def provision(name):
	_guard()
	return get_provider(name).provision()


@frappe.whitelist()
def sync_numbers(name):
	_guard()
	provider = get_provider(name)
	created = 0
	for n in provider.list_numbers() or []:
		number = to_e164(n.get("number"))
		if not number:
			continue
		if frappe.db.exists("Voice Number", number):
			frappe.db.set_value("Voice Number", number, "provider_account", name)
			continue
		frappe.get_doc({"doctype": "Voice Number", "number": number, "label": n.get("label") or number,
			"provider_account": name, "enabled": 1}).insert()
		created += 1
	return {"created": created}


@frappe.whitelist()
def save_identities(name, rows):
	_guard()
	rows = json.loads(rows) if isinstance(rows, str) else rows
	wanted = {r["agent"]: (r.get("identity") or "").strip() for r in rows if r.get("agent")}
	for agent_name in frappe.get_all("Voice Agent", pluck="name"):
		agent = frappe.get_doc("Voice Agent", agent_name)
		existing = next((r for r in agent.identities if r.provider_account == name), None)
		ident = wanted.get(agent_name)
		changed = False
		if ident and existing and existing.identity != ident:
			existing.identity, changed = ident, True
		elif ident and not existing:
			agent.append("identities", {"provider_account": name, "identity": ident})
			changed = True
		elif not ident and existing:
			agent.identities = [r for r in agent.identities if r.provider_account != name]
			changed = True
		if changed:
			agent.save()
	return {"ok": True}


@frappe.whitelist()
def rotate_token(name):
	_guard()
	acc = frappe.get_doc(ACC, name)
	acc.webhook_token = frappe.generate_hash(length=40)
	acc.save()
	return {"ok": True}


@frappe.whitelist()
def test_sms(name, to, message=None):
	_guard()
	from neotec_voice.core.sms import send

	return send(to, message or _("Neotec Voice Hub test message ✓"), account=name)
