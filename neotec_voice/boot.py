import frappe
from frappe.utils import cint


def has_app_permission():
	roles = set(frappe.get_roles())
	return bool(roles & {"Voice Manager", "Voice Agent", "System Manager"})


def boot_session(bootinfo):
	if frappe.session.user == "Guest":
		return
	try:
		s = frappe.get_cached_doc("Voice Settings")
		agent = frappe.db.get_value("Voice Agent", {"name": frappe.session.user, "enabled": 1},
			["status", "device", "extension", "default_account"], as_dict=True)
		bootinfo.neotec_voice = {
			"agent": agent,
			"screen_pop": cint(s.screen_pop),
			"click_to_call": cint(s.click_to_call),
		}
	except Exception:
		bootinfo.neotec_voice = {}
