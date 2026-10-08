# Copyright (c) 2026, Neotec Integrated Solutions

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from neotec_voice.core.util import split_csv


class VoiceSettings(Document):
	def validate(self):
		self.default_country = (self.default_country or "SA").upper()[:2]
		if self.ai_enabled:
			allowed = [h.lower() for h in split_csv(self.ai_allowed_hosts)]
			if not allowed:
				frappe.throw(_("List the self-hosted AI hosts allowed to receive call audio (PDPL residency)."))
			for label, url in ((_("Speech-to-text URL"), self.stt_url), (_("Ollama URL"), self.ollama_url)):
				host = (urlparse(url or "").hostname or "").lower()
				if not host:
					frappe.throw(_("{0} is required when AI is enabled.").format(label))
				if host not in allowed:
					frappe.throw(_("{0} host {1} is not in the allow-list.").format(label, host))

	def on_update(self):
		frappe.cache.delete_keys("ntv:lookup:*")
