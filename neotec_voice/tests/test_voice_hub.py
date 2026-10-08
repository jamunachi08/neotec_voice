"""bench --site <site> run-tests --app neotec_voice"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from neotec_voice.core.events import CallEvent, ingest
from neotec_voice.core.rating import rate
from neotec_voice.core.routing import resolve_inbound

ACC = "NT Test Twilio"
AGENT = "nt-agent@example.com"


def _account():
	if not frappe.db.exists("Voice Provider Account", ACC):
		frappe.get_doc({"doctype": "Voice Provider Account", "account_name": ACC, "vendor": "Twilio",
			"config": json.dumps({"account_sid": "AC00"}), "enabled": 1}).insert(ignore_permissions=True)
	return frappe.get_doc("Voice Provider Account", ACC)


def _agent():
	if not frappe.db.exists("User", AGENT):
		frappe.get_doc({"doctype": "User", "email": AGENT, "first_name": "NT Agent", "send_welcome_email": 0}).insert(ignore_permissions=True)
	if not frappe.db.exists("Voice Agent", AGENT):
		frappe.get_doc({"doctype": "Voice Agent", "user": AGENT, "device": "Mobile", "mobile_no": "+966500000001",
			"extension": "7001", "status": "Available", "enabled": 1}).insert(ignore_permissions=True)
	frappe.db.set_value("Voice Agent", AGENT, "status", "Available")


class TestVoiceHub(FrappeTestCase):
	def setUp(self):
		self.acc = _account()
		_agent()

	def test_rating_longest_prefix(self):
		if not frappe.db.exists("Voice Rate Plan", "NT Test Plan"):
			frappe.get_doc({"doctype": "Voice Rate Plan", "plan_name": "NT Test Plan", "default_rate_per_minute": 1,
				"rates": [{"prefix": "9665", "rate_per_minute": 0.5, "increment_seconds": 30, "minimum_seconds": 60,
					"connection_fee": 0.1}]}).insert(ignore_permissions=True)
		self.assertAlmostEqual(rate("NT Test Plan", "+966501234567", 75), 0.85)
		self.assertAlmostEqual(rate("NT Test Plan", "+442012345678", 61), 2.0)
		self.assertEqual(rate("NT Test Plan", "+966501234567", 0), 0)

	def test_inbound_routes_to_available_agent(self):
		target = resolve_inbound(self.acc, "+966110000000", "+966501234567")
		self.assertEqual(target.kind, "agents")
		self.assertIn(AGENT, target.users)

	def test_ingest_is_idempotent_and_monotonic(self):
		ring = CallEvent(external_id="CA-T1", kind="ringing", direction="Inbound", from_number="0501234567",
			to_number="+966110000000", ring_agents=[AGENT], raw={"n": 1})
		call = ingest(self.acc, ring)
		self.assertEqual(call.status, "Ringing")
		self.assertIsNone(ingest(self.acc, ring))  # replay ignored
		ingest(self.acc, CallEvent(external_id="CA-T1", kind="answered", agent_hint="+966500000001", raw={"n": 2}))
		ingest(self.acc, CallEvent(external_id="CA-T1", kind="ended", status="Completed", duration=40, raw={"n": 3}))
		late = ingest(self.acc, CallEvent(external_id="CA-T1", kind="ringing", raw={"n": 4}))
		self.assertEqual(late.status, "Completed")
		self.assertEqual(late.agent, AGENT)
		self.assertEqual(frappe.db.count("Voice Call", {"external_id": "CA-T1"}), 1)

	def test_unanswered_inbound_becomes_missed(self):
		ingest(self.acc, CallEvent(external_id="CA-T2", kind="ringing", direction="Inbound", from_number="0507654321", raw={"a": 1}))
		call = ingest(self.acc, CallEvent(external_id="CA-T2", kind="ended", status="No Answer", raw={"a": 2}))
		self.assertEqual(call.status, "Missed")

	def test_every_vendor_has_a_setup_page(self):
		from neotec_voice.providers import catalogue

		for meta in catalogue():
			self.assertTrue(frappe.db.exists("Page", meta["page"]), meta["page"])
