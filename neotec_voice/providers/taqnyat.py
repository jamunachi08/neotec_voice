from frappe import _

from neotec_voice.core.phone import national_digits
from neotec_voice.providers.base import F, BaseProvider, ProviderError, register

API = "https://api.taqnyat.sa"


@register
class TaqnyatProvider(BaseProvider):
	vendor = "Taqnyat"
	slug = "taqnyat"
	label = "Taqnyat"
	category = "SMS Gateway"
	color = "#00A19A"
	region = "KSA"
	sort_order = 61
	tagline = "Saudi SMS gateway"
	description = "Bulk, transactional and OTP SMS for Saudi recipients with registered senders."
	capabilities = frozenset({"sms"})
	fields = [
		F("bearer_token", "Bearer Token", "Password", secret=True, reqd=True),
		F("sms_sender", "Sender name", reqd=True),
	]

	def _h(self):
		self.require("bearer_token")
		return {"Authorization": f"Bearer {self.secret('bearer_token')}"}

	def send_sms(self, to, body, sender=None):
		res = self.http("POST", f"{API}/v1/messages", headers=self._h(), json={
			"recipients": [national_digits(to)], "body": body, "sender": sender or self.cfg("sms_sender")})
		if res.get("statusCode") not in (None, 201, 200, "201", "200"):
			raise ProviderError(res.get("message") or _("Taqnyat rejected the message"))
		return {"id": res.get("messageId"), "status": "Sent"}

	def test_connection(self):
		res = self.http("GET", f"{API}/account/balance", headers=self._h())
		return {"ok": True, "message": _("Connected · balance {0} {1}").format(res.get("balance"), res.get("currency") or "")}

	def setup_steps(self):
		return [
			_("Taqnyat portal › Developer › Applications: create an application and copy the Bearer token."),
			_("Enter an approved sender name."),
			_("Save, Test and send a test SMS."),
		]
