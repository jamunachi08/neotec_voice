"""Vendor-neutral SMS with automatic failover across SMS-capable accounts."""

import frappe
from frappe import _

from neotec_voice.core.phone import to_e164
from neotec_voice.core.util import setting

REDACTED = "[redacted]"


def sms_accounts(preferred=None):
	from neotec_voice.providers import provider_class

	names = []
	for name in [preferred, setting("default_sms_account")]:
		if name and name not in names:
			names.append(name)
	for row in frappe.get_all("Voice Provider Account", filters={"enabled": 1}, fields=["name", "vendor"],
			order_by="is_default desc, creation asc"):
		cls = provider_class(row.vendor)
		if cls and "sms" in cls.capabilities and row.name not in names:
			names.append(row.name)
	return names


def send(to, body, account=None, sender=None, reference_doctype=None, reference_name=None, redact=False):
	from neotec_voice.providers import get_provider

	dest = to_e164(to)
	if not dest or not body:
		frappe.throw(_("Recipient and message are required."))
	errors = []
	for name in sms_accounts(account):
		acc = frappe.get_doc("Voice Provider Account", name)
		if not acc.enabled:
			continue
		provider = get_provider(acc)
		if "sms" not in provider.capabilities:
			continue
		log = {
			"doctype": "Voice Message",
			"direction": "Outbound",
			"provider_account": acc.name,
			"to_number": dest,
			"from_number": sender or provider.cfg("sms_sender") or provider.cfg("default_caller_id"),
			"body": REDACTED if redact else body,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"sent_by": frappe.session.user if frappe.session.user != "Guest" else None,
		}
		try:
			res = provider.send_sms(dest, body, sender) or {}
			log.update({"status": res.get("status") or "Sent", "external_id": res.get("id")})
			frappe.get_doc(log).insert(ignore_permissions=True)
			return {"ok": True, "account": acc.name, "id": res.get("id")}
		except Exception as e:
			errors.append(f"{acc.name}: {e}")
			log.update({"status": "Failed", "error": str(e)[:500]})
			frappe.get_doc(log).insert(ignore_permissions=True)
	frappe.throw(_("SMS could not be sent.") + ("<br>" + "<br>".join(errors) if errors else ""))
