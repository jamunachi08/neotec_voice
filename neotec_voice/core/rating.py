"""Prefix-based rating engine: what a call costs us (buy) and what we charge
(sell). This is what lets an Neotec deployment operate as a voice reseller."""

import math

import frappe
from frappe.utils import cint, flt

from neotec_voice.core.phone import digits, to_e164


def _plan_rows(plan):
	cache_key = f"ntv:rateplan:{plan}"
	rows = frappe.cache.get_value(cache_key)
	if rows is None:
		doc = frappe.get_doc("Voice Rate Plan", plan)
		rows = [
			{
				"prefix": digits(r.prefix),
				"rate": flt(r.rate_per_minute),
				"increment": cint(r.increment_seconds) or 60,
				"minimum": cint(r.minimum_seconds),
				"fee": flt(r.connection_fee),
			}
			for r in doc.rates
		]
		rows.sort(key=lambda r: len(r["prefix"]), reverse=True)
		default = {
			"prefix": "",
			"rate": flt(doc.default_rate_per_minute),
			"increment": cint(doc.default_increment) or 60,
			"minimum": 0,
			"fee": 0,
		}
		rows.append(default)
		frappe.cache.set_value(cache_key, rows, expires_in_sec=3600)
	return rows


def clear_cache(plan):
	frappe.cache.delete_value(f"ntv:rateplan:{plan}")


def rate(plan, number, billable_seconds):
	"""Longest-prefix match, billed in increments with a minimum and a fee."""
	if not plan or not billable_seconds or cint(billable_seconds) <= 0:
		return 0.0
	d = digits(to_e164(number))
	for row in _plan_rows(plan):
		if d.startswith(row["prefix"]):
			secs = max(cint(billable_seconds), row["minimum"])
			units = math.ceil(secs / row["increment"])
			return round(row["fee"] + units * row["increment"] / 60.0 * row["rate"], 4)
	return 0.0
