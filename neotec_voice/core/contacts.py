"""Caller identification across any DocType (Contact, Lead, Customer,
Supplier, Employee, ...). Sources are configured in Voice Settings."""

import frappe
from frappe.utils import cint

from neotec_voice.core.phone import match_key
from neotec_voice.core.util import settings, split_csv

CACHE_TTL = 600
DEFAULT_SOURCES = [
	("Contact", "phone,mobile_no", "full_name"),
	("Lead", "mobile_no,phone,whatsapp_no", "lead_name"),
	("Customer", "mobile_no", "customer_name"),
	("Supplier", "mobile_no", "supplier_name"),
	("Employee", "cell_number,emergency_phone_number", "employee_name"),
]


def _norm(col):
	expr = f"`{col}`"
	for ch in (" ", "-", "(", ")", "+", "."):
		expr = f"REPLACE({expr}, '{ch}', '')"
	return expr


def sources():
	rows = sorted(settings().get("lookup_sources") or [], key=lambda r: cint(r.priority) or 99)
	if rows:
		return [(r.source_doctype, r.phone_fields, r.title_field) for r in rows]
	return DEFAULT_SOURCES


def lookup(number, use_cache=True):
	"""Return {party_type, party, party_name, route, links} or {}."""
	key = match_key(number)
	if len(key) < 6:
		return {}
	cache_key = f"ntv:lookup:{key}"
	if use_cache:
		cached = frappe.cache.get_value(cache_key)
		if cached is not None:
			return cached
	result = _search(key) or {}
	frappe.cache.set_value(cache_key, result, expires_in_sec=CACHE_TTL)
	return result


def forget(number):
	frappe.cache.delete_value(f"ntv:lookup:{match_key(number)}")


def _search(key):
	like = f"%{key}"
	for doctype, phone_fields, title_field in sources():
		if not doctype or not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		fields = [f for f in split_csv(phone_fields) if meta.has_field(f)]
		title = title_field if title_field and meta.has_field(title_field) else None
		title_sql = f"`{title}`" if title else "name"
		name = None
		party_name = None
		if fields:
			where = " or ".join(f"{_norm(f)} like %(like)s" for f in fields)
			rows = frappe.db.sql(
				f"select name, {title_sql} as title from `tab{doctype}` where {where} "
				"order by modified desc limit 1",
				{"like": like},
				as_dict=True,
			)
			if rows:
				name, party_name = rows[0].name, rows[0].title
		if not name and doctype == "Contact":
			rows = frappe.db.sql(
				f"select parent from `tabContact Phone` where {_norm('phone')} like %(like)s "
				"and parenttype='Contact' order by modified desc limit 1",
				{"like": like},
				as_dict=True,
			)
			if rows:
				name = rows[0].parent
				party_name = frappe.db.get_value("Contact", name, "full_name") or name
		if name:
			return _result(doctype, name, party_name)
	return None


def _result(doctype, name, party_name):
	links = []
	if doctype == "Contact":
		links = frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Contact", "parent": name},
			fields=["link_doctype", "link_name"],
			limit=5,
		)
	return {
		"party_type": doctype,
		"party": name,
		"party_name": party_name or name,
		"route": f"/app/{frappe.scrub(doctype).replace('_', '-')}/{name}",
		"links": [dict(l) for l in links],
	}
