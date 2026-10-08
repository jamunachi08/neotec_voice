# Copyright (c) 2026, Neotec Integrated Solutions

import frappe
from frappe import _
from frappe.model.document import Document


class VoiceProviderAccount(Document):
	def before_insert(self):
		self.webhook_key = frappe.generate_hash(length=20)
		if not self.webhook_token:
			self.webhook_token = frappe.generate_hash(length=40)

	def validate(self):
		from neotec_voice.providers import provider_class

		if not provider_class(self.vendor):
			frappe.throw(_("Vendor {0} is not registered. Configure accounts from the Voice Hub.").format(self.vendor))
		if not self.webhook_key:
			self.webhook_key = frappe.generate_hash(length=20)

	def on_update(self):
		if self.is_default:
			frappe.db.sql(
				"update `tabVoice Provider Account` set is_default = 0 where name != %s and is_default = 1", self.name
			)
		frappe.cache.delete_keys(f"ntv:*:{self.name}:token")

	@frappe.whitelist()
	def get_setup_route(self):
		from neotec_voice.providers import provider_class

		return provider_class(self.vendor).meta()["page"]
