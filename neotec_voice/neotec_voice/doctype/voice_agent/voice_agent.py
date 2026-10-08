# Copyright (c) 2026, Neotec Integrated Solutions

import frappe
from frappe import _
from frappe.model.document import Document

from neotec_voice.core.phone import to_e164


class VoiceAgent(Document):
	def validate(self):
		if self.mobile_no:
			self.mobile_no = to_e164(self.mobile_no)
		seen = set()
		for row in self.identities:
			if row.provider_account in seen:
				frappe.throw(_("Only one identity per provider account (row {0}).").format(row.idx))
			seen.add(row.provider_account)
			clash = frappe.db.sql(
				"""select parent from `tabVoice Agent Identity` where provider_account=%s and identity=%s and parent!=%s""",
				(row.provider_account, row.identity, self.name),
			)
			if clash:
				frappe.throw(_("Identity {0} on {1} is already used by {2}.").format(row.identity, row.provider_account, clash[0][0]))
		if self.device == "Mobile" and not self.mobile_no:
			frappe.msgprint(_("Agents ringing on Mobile need a mobile number."), indicator="orange", alert=True)

	def after_insert(self):
		if not frappe.db.exists("Has Role", {"parent": self.user, "role": "Voice Agent"}):
			user = frappe.get_doc("User", self.user)
			user.add_roles("Voice Agent")
