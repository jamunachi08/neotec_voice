"""Reseller billing: usage (rated per call) + DID rental, as draft Sales
Invoices per Customer. Only active when ERPNext is installed and billing is
enabled in Voice Settings."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate, nowdate

from neotec_voice.core.util import settings


def run_monthly():
	"""Scheduler: bill the previous calendar month."""
	prev = add_months(getdate(nowdate()), -1)
	run(str(get_first_day(prev)), str(get_last_day(prev)))


def run(period_start, period_end, submit=False):
	s = settings()
	if not s.billing_enabled or "erpnext" not in frappe.get_installed_apps():
		return []
	if not (s.usage_item and frappe.db.exists("Item", s.usage_item)):
		frappe.throw(_("Set a valid Usage Item in Voice Settings."))
	period = str(period_start)[:7]
	calls = frappe.get_all(
		"Voice Call",
		filters={
			"billed": 0,
			"billing_party_type": "Customer",
			"billing_party": ["is", "set"],
			"sell_amount": [">", 0],
			"ended_at": ["between", [period_start, f"{period_end} 23:59:59"]],
		},
		fields=["name", "billing_party", "sell_amount", "talk_time"],
	)
	usage = defaultdict(lambda: {"amount": 0.0, "calls": [], "seconds": 0})
	for c in calls:
		u = usage[c.billing_party]
		u["amount"] += flt(c.sell_amount)
		u["seconds"] += c.talk_time or 0
		u["calls"].append(c.name)
	rentals = defaultdict(list)
	for n in frappe.get_all(
		"Voice Number",
		filters={"billing_party_type": "Customer", "billing_party": ["is", "set"], "monthly_fee": [">", 0], "enabled": 1},
		fields=["name", "billing_party", "monthly_fee", "last_billed_period"],
	):
		if n.last_billed_period != period:
			rentals[n.billing_party].append(n)

	invoices = []
	for customer in set(usage) | set(rentals):
		si = frappe.new_doc("Sales Invoice")
		si.customer = customer
		if s.billing_company:
			si.company = s.billing_company
		si.set_posting_time = 1
		si.posting_date = period_end
		si.due_date = period_end
		u = usage.get(customer)
		if u and u["amount"] > 0:
			si.append("items", {
				"item_code": s.usage_item,
				"qty": 1,
				"rate": round(u["amount"], 2),
				"description": _("Voice usage {0}: {1} calls, {2} minutes").format(
					period, len(u["calls"]), round(u["seconds"] / 60, 1)),
			})
		for n in rentals.get(customer, []):
			si.append("items", {
				"item_code": s.rental_item or s.usage_item,
				"qty": 1,
				"rate": flt(n.monthly_fee),
				"description": _("Number rental {0} — {1}").format(n.name, period),
			})
		if not si.items:
			continue
		si.flags.ignore_permissions = True
		si.insert()
		if submit:
			si.submit()
		for name in (u or {}).get("calls", []):
			frappe.db.set_value("Voice Call", name, {"billed": 1, "sales_invoice": si.name}, update_modified=False)
		for n in rentals.get(customer, []):
			frappe.db.set_value("Voice Number", n.name, "last_billed_period", period, update_modified=False)
		invoices.append(si.name)
	frappe.db.commit()
	return invoices
