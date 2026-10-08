"""One-time passwords over SMS or Email. Codes are stored only as PBKDF2
hashes, resends never reset the attempt budget, and every failure looks the
same to the caller so recipients cannot be enumerated."""

import secrets
import string

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from neotec_voice.core.phone import to_e164
from neotec_voice.core.util import setting

OTP = "Voice OTP"
GENERIC_FAILURE = {"verified": False, "reason": "invalid_or_expired"}


def _hash(code):
	from passlib.hash import pbkdf2_sha256

	return pbkdf2_sha256.hash(code)


def _check(code, hashed):
	from passlib.hash import pbkdf2_sha256

	try:
		return pbkdf2_sha256.verify(code, hashed)
	except ValueError:
		return False


def _recipient(channel, recipient):
	if channel == "Email":
		from frappe.utils import validate_email_address

		email = (recipient or "").strip().lower()
		return email if validate_email_address(email) else ""
	return to_e164(recipient)


def generate(recipient, channel="SMS", purpose="Verification"):
	channel = "Email" if channel == "Email" else "SMS"
	purpose = (purpose or "Verification").strip()[:140]
	rcpt = _recipient(channel, recipient)
	if not rcpt:
		frappe.throw(_("Invalid recipient."))
	length = min(max(cint(setting("otp_length", 6)), 4), 10)
	expiry = cint(setting("otp_expiry_seconds", 300)) or 300
	max_attempts = cint(setting("otp_max_attempts", 5)) or 5

	spent = 0
	for row in frappe.get_all(OTP, filters={"recipient": rcpt, "purpose": purpose, "status": "Pending"},
			fields=["name", "attempts", "expires_on"], for_update=True):
		if get_datetime(row.expires_on) > now_datetime():
			spent = max(spent, cint(row.attempts))
		frappe.db.set_value(OTP, row.name, "status", "Expired", update_modified=False)
	if spent >= max_attempts:
		return {"sent": True}  # uniform response; the budget is exhausted

	code = "".join(secrets.choice(string.digits) for _ in range(length))
	frappe.get_doc({
		"doctype": OTP,
		"recipient": rcpt,
		"channel": channel,
		"purpose": purpose,
		"code_hash": _hash(code),
		"attempts": spent,
		"max_attempts": max_attempts,
		"expires_on": add_to_date(now_datetime(), seconds=expiry),
		"status": "Pending",
	}).insert(ignore_permissions=True)

	template = setting("otp_template") or "{code} is your verification code. Valid for {minutes} minutes."
	text = template.format(code=code, minutes=max(1, expiry // 60), purpose=purpose)
	if channel == "Email":
		frappe.sendmail(recipients=[rcpt], subject=setting("otp_email_subject") or _("Your verification code"),
			message=text, now=True)
	else:
		from neotec_voice.core.sms import send

		send(rcpt, text, redact=True)
	return {"sent": True}


def verify(recipient, code, channel="SMS", purpose="Verification"):
	channel = "Email" if channel == "Email" else "SMS"
	rcpt = _recipient(channel, recipient)
	purpose = (purpose or "Verification").strip()[:140]
	if not rcpt or not code:
		return GENERIC_FAILURE
	rows = frappe.get_all(OTP, filters={"recipient": rcpt, "purpose": purpose, "status": "Pending"},
		fields=["name", "code_hash", "attempts", "max_attempts", "expires_on"],
		order_by="creation desc", limit=1, for_update=True)
	if not rows:
		_check("0", _hash("x"))  # equalise timing
		return GENERIC_FAILURE
	row = rows[0]
	if get_datetime(row.expires_on) < now_datetime() or cint(row.attempts) >= cint(row.max_attempts):
		frappe.db.set_value(OTP, row.name, "status", "Expired", update_modified=False)
		return GENERIC_FAILURE
	if _check(str(code).strip(), row.code_hash):
		frappe.db.set_value(OTP, row.name, {"status": "Verified", "verified_on": now_datetime()}, update_modified=False)
		return {"verified": True}
	attempts = cint(row.attempts) + 1
	frappe.db.set_value(OTP, row.name, {"attempts": attempts,
		"status": "Locked" if attempts >= cint(row.max_attempts) else "Pending"}, update_modified=False)
	return GENERIC_FAILURE
