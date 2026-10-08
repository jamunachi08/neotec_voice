"""Daily retention: drop recordings past the configured window (PDPL)."""

import frappe
from frappe.utils import add_days, cint, now_datetime

from neotec_voice.core.util import settings


def run():
	days = cint(settings().recording_retention_days)
	if days <= 0:
		return
	cutoff = add_days(now_datetime(), -days)
	calls = frappe.get_all(
		"Voice Call",
		filters={"ended_at": ["<", cutoff], "recording_purged": 0},
		or_filters={"recording_url": ["is", "set"], "recording_file": ["is", "set"]},
		pluck="name",
		limit=500,
	)
	for name in calls:
		for f in frappe.get_all("File", filters={"attached_to_doctype": "Voice Call", "attached_to_name": name}, pluck="name"):
			frappe.delete_doc("File", f, ignore_permissions=True, force=True)
		frappe.db.set_value(
			"Voice Call", name, {"recording_url": None, "recording_file": None, "recording_purged": 1}, update_modified=False
		)
	frappe.db.commit()
