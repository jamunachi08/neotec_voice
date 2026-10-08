"""Provider registry.

Built-in providers register themselves with ``@register``. Any other Frappe
app can contribute a provider without touching this app:

    # hooks.py of your app
    neotec_voice_providers = ["my_app.voice.MyCarrierProvider"]

The class must subclass ``neotec_voice.providers.base.BaseProvider``.
"""

import importlib

import frappe

_REGISTRY = {}
BUILTIN_MODULES = (
	"threecx",
	"asterisk",
	"freeswitch",
	"yeastar",
	"twilio",
	"exotel",
	"vonage",
	"plivo",
	"telnyx",
	"unifonic",
	"taqnyat",
	"generic_http",
)
_loaded = False


def register(cls):
	_REGISTRY[cls.vendor] = cls
	return cls


def _load():
	global _loaded
	if _loaded:
		return
	for mod in BUILTIN_MODULES:
		importlib.import_module(f"neotec_voice.providers.{mod}")
	try:
		for path in frappe.get_hooks("neotec_voice_providers") or []:
			register(frappe.get_attr(path))
	except Exception:
		frappe.log_error(title="Neotec Voice: failed loading external provider")
	_loaded = True


def registry():
	_load()
	return dict(_REGISTRY)


def provider_class(vendor):
	_load()
	return _REGISTRY.get(vendor)


def get_provider(account):
	"""``account`` is a Voice Provider Account doc or name."""
	if isinstance(account, str):
		account = frappe.get_doc("Voice Provider Account", account)
	cls = provider_class(account.vendor)
	if not cls:
		frappe.throw(frappe._("No provider is registered for vendor {0}").format(account.vendor))
	return cls(account)


def catalogue():
	"""Metadata for the Hub page and the vendor setup pages."""
	return [cls.meta() for cls in sorted(registry().values(), key=lambda c: (c.sort_order, c.label))]
