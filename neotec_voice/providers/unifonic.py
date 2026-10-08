from frappe import _

from neotec_voice.core.phone import national_digits
from neotec_voice.providers.base import F, BaseProvider, ProviderError, register


@register
class UnifonicProvider(BaseProvider):
	vendor = "Unifonic"
	slug = "unifonic"
	label = "Unifonic"
	category = "SMS Gateway"
	color = "#5B2C83"
	region = "KSA · GCC"
	sort_order = 60
	tagline = "Saudi CPaaS — CITC-registered sender IDs"
	description = "Transactional and OTP SMS through Unifonic with a registered Sender ID."
	capabilities = frozenset({"sms"})
	fields = [
		F("app_sid", "AppSid", "Password", secret=True, reqd=True),
		F("sms_sender", "Sender ID", reqd=True, description="Must be registered with Unifonic/CST"),
		F("base_url", "API base URL", default="https://el.cloud.unifonic.com"),
	]

	def _base(self):
		return (self.cfg("base_url") or "https://el.cloud.unifonic.com").rstrip("/")

	def send_sms(self, to, body, sender=None):
		self.require("app_sid", "sms_sender")
		res = self.http("POST", f"{self._base()}/rest/SMS/messages", data={
			"AppSid": self.secret("app_sid"), "SenderID": sender or self.cfg("sms_sender"),
			"Recipient": national_digits(to), "Body": body, "responseType": "JSON"})
		if str(res.get("success")).lower() not in ("true", "1"):
			raise ProviderError(res.get("message") or res.get("errorCode") or _("Unifonic rejected the message"))
		return {"id": (res.get("data") or {}).get("MessageID"), "status": "Sent"}

	def test_connection(self):
		self.require("app_sid", "sms_sender")
		return {"ok": True, "message": _("Credentials saved. Send a test SMS from the page to verify end-to-end.")}

	def setup_steps(self):
		return [
			_("Unifonic Console › Applications: create a REST app and copy its AppSid."),
			_("Use a Sender ID approved for your account (CST registration in KSA)."),
			_("Optionally set this account as Default SMS Account in Voice Settings for OTP and notifications."),
			_("Use 'Send test SMS' on this page to confirm delivery."),
		]
