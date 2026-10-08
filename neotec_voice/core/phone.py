"""Phone number normalisation. Everything stored by Neotec Voice is E.164
where possible, so matching never depends on how a vendor formats numbers."""

import re

import frappe

from neotec_voice.core.util import digits, setting

try:
	import phonenumbers
except ImportError:  # pragma: no cover - phonenumbers ships with Frappe
	phonenumbers = None


def default_country():
	return (setting("default_country", "SA") or "SA").upper()


def strip_uri(value):
	"""'sip:1001@pbx.local' -> '1001', 'client:ali(at)x.com' -> 'ali@x.com'."""
	text = str(value or "").strip()
	for prefix in ("sip:", "sips:", "tel:", "client:"):
		if text.lower().startswith(prefix):
			text = text[len(prefix):]
			break
	text = text.replace("(at)", "@")
	if "@" in text and re.match(r"^\+?\d+@", text):
		text = text.split("@", 1)[0]
	return text


def is_extension(value):
	return bool(re.fullmatch(r"\d{2,6}", str(value or "").strip()))


def to_e164(number, country=None):
	"""Best-effort E.164. Extensions, SIP users and anything unparseable are
	returned cleaned but otherwise untouched."""
	if not number:
		return ""
	raw = strip_uri(number)
	if "@" in raw or is_extension(raw):
		return raw
	cleaned = re.sub(r"[^\d+]", "", raw)
	if cleaned.startswith("00"):
		cleaned = "+" + cleaned[2:]
	if not cleaned:
		return raw
	if phonenumbers:
		try:
			parsed = phonenumbers.parse(cleaned, country or default_country())
			if phonenumbers.is_possible_number(parsed):
				return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
		except phonenumbers.NumberParseException:
			pass
	return cleaned


def match_key(number):
	"""Last 9 significant digits: stable across +966 / 00966 / 0 prefixes."""
	d = digits(strip_uri(number))
	return d[-9:] if len(d) >= 9 else d


def same_number(a, b):
	ka, kb = match_key(a), match_key(b)
	return bool(ka) and ka == kb


def national_digits(number):
	"""Digits without '+', as several gateways (Vonage, Unifonic) expect."""
	return digits(to_e164(number))


def find_did(number):
	"""Resolve a Voice Number record from any formatting of the number."""
	if not number:
		return None
	e164 = to_e164(number)
	if frappe.db.exists("Voice Number", e164):
		return e164
	key = match_key(number)
	if len(key) < 6:
		return None
	for row in frappe.get_all("Voice Number", fields=["name"], filters={"enabled": 1}):
		if match_key(row.name) == key:
			return row.name
	return None
