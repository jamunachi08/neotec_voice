# Copyright (c) 2026, Neotec Integrated Solutions

import frappe
from frappe import _
from frappe.model.document import Document

from neotec_voice.core.util import digits


class VoiceRatePlan(Document):
	def validate(self):
		seen = set()
		for row in self.rates:
			p = digits(row.prefix)
			if not p:
				frappe.throw(_("Row {0}: prefix must contain digits.").format(row.idx))
			if p in seen:
				frappe.throw(_("Row {0}: duplicate prefix {1}.").format(row.idx, row.prefix))
			seen.add(p)
			row.increment_seconds = row.increment_seconds or 60

	def on_update(self):
		from neotec_voice.core.rating import clear_cache

		clear_cache(self.name)
