"""The provider contract. A provider translates between Neotec's normalised
model (CallEvent, Target, agent payloads) and one vendor's API.

Capabilities (declare only what is really implemented):
  outbound   click-to-call / originate
  inbound    vendor asks Neotec where to send an inbound call
  events     vendor posts call events/CDRs to Neotec
  sms        send SMS
  recording  recordings retrievable
  softphone  agents can use a browser softphone
  numbers    list numbers on the account
  hangup     terminate a live call
  provision  Neotec can push its webhook URLs into the vendor
  crm        vendor queries Neotec for caller lookup (CRM-template style)
"""

import json
from dataclasses import dataclass, field
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_url

from neotec_voice.providers import register  # noqa: F401  (re-export for providers)


class ProviderError(Exception):
	pass


@dataclass
class WebhookResult:
	events: list = field(default_factory=list)
	body: str | bytes | None = None
	content_type: str = "text/plain"
	status: int = 200


def F(fieldname, label, fieldtype="Data", secret=False, reqd=False, default=None, description=None, options=None,
		placeholder=None):
	"""Declarative config field rendered on the vendor setup page."""
	return {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": fieldtype,
		"secret": bool(secret),
		"reqd": 1 if reqd else 0,
		"default": default,
		"description": description,
		"options": options,
		"placeholder": placeholder,
	}


def x(tag, children=None, **attrs):
	"""Tiny XML builder for TwiML / Plivo XML. Attribute names are used as-is."""
	parts = []
	for k, v in attrs.items():
		if v is None or v == "":
			continue
		if isinstance(v, bool):
			v = "true" if v else "false"
		parts.append(f"{k}={quoteattr(str(v))}")
	head = f"<{tag}{' ' if parts else ''}{' '.join(parts)}"
	if children is None or children == []:
		return head + "/>"
	if isinstance(children, (list, tuple)):
		inner = "".join(c for c in children if c)
	else:
		inner = escape(str(children))
	return f"{head}>{inner}</{tag}>"


def xml_doc(root):
	return '<?xml version="1.0" encoding="UTF-8"?>' + root


def secret_fieldname(key):
	return f"nts_{key}"


class BaseProvider:
	vendor = None  # stored on Voice Provider Account.vendor
	slug = None  # page route: /app/voice-<slug>
	label = None
	category = "Cloud CPaaS"  # Cloud CPaaS | IP-PBX | SMS Gateway | Open Connector
	color = "#4b5563"
	tagline = ""
	description = ""
	region = "Global"
	sort_order = 50
	capabilities = frozenset()
	fields = []
	docs_url = None

	def __init__(self, account):
		self.account = account
		try:
			self.config = json.loads(account.config or "{}") or {}
		except ValueError:
			self.config = {}

	# ----- metadata --------------------------------------------------------
	@classmethod
	def meta(cls):
		return {
			"vendor": cls.vendor,
			"slug": cls.slug,
			"page": f"voice-{cls.slug.replace('_', '-')}",
			"label": cls.label or cls.vendor,
			"category": cls.category,
			"color": cls.color,
			"tagline": cls.tagline,
			"description": cls.description,
			"region": cls.region,
			"capabilities": sorted(cls.capabilities),
			"fields": cls.fields,
			"docs_url": cls.docs_url,
		}

	@classmethod
	def secret_fields(cls):
		return [f["fieldname"] for f in cls.fields if f.get("secret")]

	# ----- config ----------------------------------------------------------
	def cfg(self, key, default=None):
		value = self.config.get(key)
		if value in (None, ""):
			declared = next((f.get("default") for f in self.fields if f["fieldname"] == key), None)
			return default if declared in (None, "") else declared
		return value

	def flag(self, key, default=0):
		return bool(cint(self.cfg(key, default)))

	def secret(self, key):
		"""Secrets live in Frappe's encrypted password store (__Auth), one entry
		per account and field, never in the config JSON."""
		if not self.account.get("name"):
			return None
		from frappe.utils.password import get_decrypted_password

		return get_decrypted_password(
			"Voice Provider Account", self.account.name, secret_fieldname(key), raise_exception=False
		)

	def require(self, *keys):
		missing = [k for k in keys if not (self.cfg(k) or self.secret(k))]
		if missing:
			labels = {f["fieldname"]: f["label"] for f in self.fields}
			raise ProviderError(_("{0}: missing {1}").format(self.label, ", ".join(_(labels.get(m, m)) for m in missing)))

	def webhook_url(self, phase="event", **extra):
		query = {
			"k": self.account.webhook_key,
			"t": self.account.get_password("webhook_token", raise_exception=False),
			"phase": phase,
		}
		query.update({k: v for k, v in extra.items() if v not in (None, "")})
		return get_url("/api/method/neotec_voice.api.webhook.receive") + "?" + urlencode(query)

	# ----- HTTP ------------------------------------------------------------
	def http(self, method, url, expect_json=True, ok=(200, 201, 202, 204), **kw):
		kw.setdefault("timeout", 15)
		try:
			r = requests.request(method, url, **kw)
		except requests.RequestException as e:
			raise ProviderError(_("{0}: network error — {1}").format(self.label, e)) from e
		if r.status_code not in ok:
			raise ProviderError(f"{self.label} HTTP {r.status_code}: {r.text[:400]}")
		if not expect_json:
			return r
		if not r.content:
			return {}
		try:
			return r.json()
		except ValueError:
			return {"_raw": r.text}

	# ----- routing helpers -------------------------------------------------
	def route_inbound(self, to_number, from_number):
		from neotec_voice.core.routing import resolve_inbound

		return resolve_inbound(self.account, to_number, from_number)

	# ----- contract (override) --------------------------------------------
	def _unsupported(self, what):
		raise ProviderError(_("{0} does not support {1}").format(self.label, what))

	def test_connection(self):
		return {"ok": True, "message": _("Configuration saved (no live test available for this vendor).")}

	def make_call(self, agent, to, caller_id, call_name):
		self._unsupported(_("outbound calls"))

	def hangup(self, call):
		self._unsupported(_("hang-up"))

	def send_sms(self, to, body, sender=None):
		self._unsupported(_("SMS"))

	def list_numbers(self):
		self._unsupported(_("number listing"))

	def provision(self):
		self._unsupported(_("automatic provisioning"))

	def verify_signature(self, request, payload):
		return True

	def handle_webhook(self, phase, payload, request):
		return WebhookResult()

	def fetch_recording(self, url):
		r = self.http("GET", url, expect_json=False, timeout=120)
		return r.content

	def webrtc_config(self, agent_doc):
		wss = self.cfg("wss_url")
		if not wss:
			return None
		return {
			"kind": "sip",
			"wss_url": wss,
			"domain": self.cfg("sip_domain"),
			"username": agent_doc.sip_username or agent_doc.extension,
			"password": agent_doc.get_password("sip_password", raise_exception=False),
			"display_name": agent_doc.full_name,
			"stun": self.cfg("stun_server") or "stun:stun.l.google.com:19302",
		}

	# ----- setup page content ---------------------------------------------
	def webhooks(self):
		"""[(label, phase, note)] shown on the setup page with copy buttons."""
		return [(_("Call events"), "event", "")]

	def setup_steps(self):
		return []

	def snippets(self):
		"""[{title, language, filename, content}] generated config to paste."""
		return []


SOFTPHONE_FIELDS = [
	F("wss_url", "WebRTC WSS URL (browser softphone)", placeholder="wss://pbx.example.sa:8089/ws"),
	F("sip_domain", "SIP Domain", placeholder="pbx.example.sa"),
	F("stun_server", "STUN/TURN Server", placeholder="stun:stun.l.google.com:19302"),
]
