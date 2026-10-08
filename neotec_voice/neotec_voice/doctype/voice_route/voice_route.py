# Copyright (c) 2026, Neotec Integrated Solutions

import frappe
from frappe import _
from frappe.model.document import Document


class VoiceRoute(Document):
	def validate(self):
		need = {"Queue": "target_queue", "Agent": "target_agent", "Number": "target_number"}.get(self.target_type)
		if need and not self.get(need):
			frappe.throw(_("Set the {0} for this route.").format(_(self.target_type)))
		if bool(self.from_time) != bool(self.to_time):
			frappe.throw(_("Set both From and To, or neither."))
