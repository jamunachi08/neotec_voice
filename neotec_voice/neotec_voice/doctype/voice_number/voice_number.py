# Copyright (c) 2026, Neotec Integrated Solutions

from frappe.model.document import Document

from neotec_voice.core.phone import to_e164


class VoiceNumber(Document):
	def autoname(self):
		self.number = to_e164(self.number)
		self.name = self.number

	def validate(self):
		if not self.label:
			self.label = self.number
