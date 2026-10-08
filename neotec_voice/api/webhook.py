"""Single webhook gateway for every vendor.

    /api/method/neotec_voice.api.webhook.receive?k=<account key>&t=<token>&phase=<phase>

``k`` identifies the provider account, ``t`` is a per-account secret
(constant-time compared) and, where the vendor signs requests (Twilio,
Telnyx), the signature is verified as well."""

import hmac
import json

import frappe
from werkzeug.wrappers import Response

from neotec_voice.core.events import ingest
from neotec_voice.providers import get_provider

RESERVED = {"cmd", "k", "t", "phase"}


def _payload():
	data = {}
	req = frappe.request
	if req.args:
		data.update(req.args.to_dict())
	if req.form:
		data.update(req.form.to_dict())
	if req.data and (req.mimetype or "").endswith("json"):
		try:
			body = json.loads(req.get_data(as_text=True) or "{}")
			if isinstance(body, dict):
				data.update(body)
			elif isinstance(body, list):
				data["items"] = body
		except ValueError:
			pass
	return {k: v for k, v in data.items() if k not in RESERVED}


def _deny(status=403, text="Forbidden"):
	return Response(text, status=status, mimetype="text/plain")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def receive(k=None, t=None, phase="event", **kwargs):
	if not k or not t:
		return _deny()
	name = frappe.db.get_value("Voice Provider Account", {"webhook_key": k, "enabled": 1}, "name")
	if not name:
		return _deny(404, "Unknown account")
	account = frappe.get_doc("Voice Provider Account", name)
	expected = account.get_password("webhook_token", raise_exception=False) or ""
	if not hmac.compare_digest(str(t), expected):
		return _deny()

	provider = get_provider(account)
	payload = _payload()
	if not provider.verify_signature(frappe.request, payload):
		frappe.log_error(title=f"Neotec Voice: bad signature ({name})", message=json.dumps(payload, default=str)[:4000])
		return _deny(401, "Bad signature")

	frappe.set_user("Administrator")
	try:
		result = provider.handle_webhook(phase or "event", payload, frappe.request)
		for ev in result.events:
			ingest(account, ev)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Neotec Voice webhook {name}/{phase}", message=frappe.get_traceback() + "\n\n"
			+ json.dumps(payload, default=str)[:4000])
		frappe.db.commit()
		return Response("error", status=200, mimetype="text/plain")  # never make vendors retry-storm
	finally:
		frappe.set_user("Guest")

	body = result.body if result.body is not None else "OK"
	return Response(body, status=result.status, mimetype=result.content_type)
