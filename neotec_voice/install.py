"""Idempotent seeders: safe to run on every migrate."""

import frappe

ROLES = ("Voice Manager", "Voice Agent")
DISPOSITIONS = [
	("Resolved", "تم الحل", 1, "#16a34a"),
	("Interested", "مهتم", 1, "#2563eb"),
	("Callback requested", "طلب معاودة الاتصال", 0, "#d97706"),
	("Not interested", "غير مهتم", 0, "#6b7280"),
	("Wrong number", "رقم خاطئ", 0, "#9ca3af"),
	("Escalated", "تم التصعيد", 0, "#dc2626"),
]


def after_install():
	after_migrate()


def after_migrate():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
	for name, ar, ok, color in DISPOSITIONS:
		if not frappe.db.exists("Voice Disposition", name):
			frappe.get_doc({"doctype": "Voice Disposition", "disposition": name, "disposition_ar": ar,
				"is_success": ok, "color": color}).insert(ignore_permissions=True)
	_seed_settings()
	_seed_workspace()
	frappe.db.commit()


def _seed_settings():
	from neotec_voice.core.contacts import DEFAULT_SOURCES

	s = frappe.get_single("Voice Settings")
	changed = False
	if not s.lookup_sources:
		for i, (dt, fields, title) in enumerate(DEFAULT_SOURCES, start=1):
			if frappe.db.exists("DocType", dt):
				s.append("lookup_sources", {"source_doctype": dt, "phone_fields": fields, "title_field": title, "priority": i * 10})
				changed = True
	if not s.default_country:
		s.default_country = "SA"
		changed = True
	if changed:
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)


def _seed_workspace():
	if frappe.db.exists("Workspace", "Neotec Voice"):
		return
	import json

	shortcuts = [
		("Voice Hub", "Page", "voice-hub"),
		("Voice Console", "Page", "voice-console"),
		("Call Log", "DocType", "Voice Call"),
		("Agents", "DocType", "Voice Agent"),
	]
	cards = {
		"Routing": ["Voice Number", "Voice Queue", "Voice Route", "Voice Outbound Route"],
		"Billing": ["Voice Rate Plan", "Voice Call"],
		"Setup": ["Voice Provider Account", "Voice Agent", "Voice Disposition", "Voice Settings"],
	}
	content = [{"id": "ntv-h", "type": "header", "data": {"text": '<span class="h4"><b>Neotec Voice</b></span>', "col": 12}}]
	content += [{"id": f"ntv-s{i}", "type": "shortcut", "data": {"shortcut_name": s[0], "col": 3}} for i, s in enumerate(shortcuts)]
	content += [{"id": f"ntv-c{i}", "type": "card", "data": {"card_name": c, "col": 4}} for i, c in enumerate(cards)]
	links = []
	for card, dts in cards.items():
		links.append({"type": "Card Break", "label": card, "link_count": len(dts)})
		links += [{"type": "Link", "label": dt, "link_type": "DocType", "link_to": dt} for dt in dts]
	try:
		frappe.get_doc({
			"doctype": "Workspace",
			"name": "Neotec Voice",
			"label": "Neotec Voice",
			"title": "Neotec Voice",
			"module": "Neotec Voice",
			"public": 1,
			"icon": "call",
			"content": json.dumps(content),
			"shortcuts": [{"label": l, "type": t, "link_to": to} for l, t, to in shortcuts],
			"links": links,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Neotec Voice: workspace seed skipped")
