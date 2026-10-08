import hashlib
import json
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import add_to_date, get_url

from neotec_voice.core.contacts import lookup
from neotec_voice.core.events import CallEvent
from neotec_voice.core.util import seconds, to_dt
from neotec_voice.providers.base import F, SOFTPHONE_FIELDS, BaseProvider, ProviderError, WebhookResult, register


@register
class ThreeCXProvider(BaseProvider):
	vendor = "3CX"
	slug = "3cx"
	label = "3CX"
	category = "IP-PBX"
	color = "#0596D5"
	sort_order = 1
	tagline = "On-prem or hosted 3CX v20 PBX"
	description = (
		"Click-to-call through the Call Control API, live caller lookup + screen-pop and automatic call journaling "
		"through a generated 3CX CRM template — no middleware."
	)
	capabilities = frozenset({"outbound", "events", "crm"})
	fields = [
		F("fqdn", "PBX FQDN", reqd=True, placeholder="company.3cx.sa"),
		F("client_id", "API Client ID", description="Admin › Integrations › API › Add (Call Control access)"),
		F("client_secret", "API Client Secret", "Password", secret=True),
		F("use_device_endpoint", "Ring the agent's first registered device", "Check", default=1),
		F("journal_missed", "Journal missed / not-answered calls", "Check", default=1),
		F("crm_number_length", "Lookup: significant digits", "Int", default=9),
	] + SOFTPHONE_FIELDS

	def _base(self):
		self.require("fqdn")
		host = self.cfg("fqdn").replace("https://", "").replace("http://", "").strip("/")
		return f"https://{host}"

	def _token(self):
		self.require("client_id", "client_secret")
		ck = f"ntv:3cx:{self.account.name}:token"
		tok = frappe.cache.get_value(ck)
		if tok:
			return tok
		res = self.http("POST", f"{self._base()}/connect/token", data={
			"client_id": self.cfg("client_id"), "client_secret": self.secret("client_secret"),
			"grant_type": "client_credentials"})
		tok = res.get("access_token")
		if not tok:
			raise ProviderError(_("3CX did not return an access token."))
		frappe.cache.set_value(ck, tok, expires_in_sec=max(60, int(res.get("expires_in") or 3600) - 60))
		return tok

	def _h(self):
		return {"Authorization": f"Bearer {self._token()}"}

	def test_connection(self):
		frappe.cache.delete_value(f"ntv:3cx:{self.account.name}:token")
		self._token()
		try:
			dns = self.http("GET", f"{self._base()}/callcontrol", headers=self._h())
			n = len(dns) if isinstance(dns, list) else "?"
			return {"ok": True, "message": _("Token OK · Call Control reachable · {0} monitored DNs").format(n)}
		except ProviderError as e:
			return {"ok": False, "message": _("Token OK but Call Control failed (needs 8SC+ Enterprise licence and DN access): {0}").format(e)}

	def make_call(self, agent, to, caller_id, call_name):
		dn = agent.get("identity") or agent.get("extension")
		if not dn:
			raise ProviderError(_("Agent has no 3CX extension mapped."))
		base = f"{self._base()}/callcontrol/{quote(str(dn))}"
		url = f"{base}/makecall"
		if self.flag("use_device_endpoint", 1):
			try:
				devices = self.http("GET", f"{base}/devices", headers=self._h())
				if isinstance(devices, list) and devices and devices[0].get("device_id"):
					url = f"{base}/devices/{quote(devices[0]['device_id'], safe='')}/makecall"
			except ProviderError:
				pass
		res = self.http("POST", url, headers=self._h(), json={"destination": to, "reason": "Neotec click-to-call", "timeout": 30})
		result = res.get("result") or {}
		return str(result.get("callid") or result.get("id") or f"3cx-{call_name}")

	# 3CX CRM integration: lookup (screen-pop) + journaling
	def handle_webhook(self, phase, p, request):
		if phase == "lookup":
			hit = lookup(p.get("number"))
			contacts = []
			if hit:
				first, _sep, last = (hit["party_name"] or "").partition(" ")
				company = next((l["link_name"] for l in hit["links"] if l["link_doctype"] == "Customer"), "")
				contacts.append({
					"id": f"{hit['party_type']}::{hit['party']}",
					"first_name": first,
					"last_name": last,
					"company": company or (hit["party_name"] if hit["party_type"] in ("Customer", "Supplier") else ""),
					"phone": p.get("number"),
					"url": get_url(hit["route"]),
					"entity_type": hit["party_type"],
				})
			return WebhookResult([], json.dumps({"contacts": contacts}), "application/json")
		if phase == "journal":
			ct = (p.get("call_type") or "").title()
			if ct in ("Missed", "Notanswered") and not self.flag("journal_missed", 1):
				return WebhookResult([], json.dumps({"ok": True}), "application/json")
			dur = seconds(p.get("duration")) or 0
			start = to_dt(p.get("start"))
			direction = "Outbound" if ct in ("Outbound", "Notanswered") else "Inbound"
			status = {"Missed": "Missed", "Notanswered": "No Answer"}.get(ct, "Completed")
			raw_id = f"{p.get('number')}|{p.get('agent')}|{p.get('start')}|{ct}"
			ext_id = "3cx-" + hashlib.sha1(raw_id.encode()).hexdigest()[:24]
			ev = CallEvent(
				external_id=ext_id, kind="ended", status=status, direction=direction,
				from_number=p.get("number") if direction == "Inbound" else None,
				to_number=p.get("number") if direction == "Outbound" else None,
				agent_hint=p.get("agent"), talk_time=dur if status == "Completed" else 0, duration=dur,
				started_at=start, answered_at=start if status == "Completed" and dur else None,
				ended_at=add_to_date(start, seconds=dur) if start else None, raw=p)
			return WebhookResult([ev], json.dumps({"ok": True}), "application/json")
		return WebhookResult()

	def webhooks(self):
		return [
			(_("CRM lookup (screen-pop)"), "lookup", _("Embedded in the generated CRM template.")),
			(_("CRM call journaling"), "journal", _("Embedded in the generated CRM template.")),
		]

	def setup_steps(self):
		return [
			_("Click-to-call: 3CX Admin › Integrations › API › Add client application; enable 3CX Call Control API access and select the agent extensions. Requires an 8SC+ Enterprise licence."),
			_("Paste the Client ID and the API key (secret) here, Save, then Test."),
			_("Screen-pop + journaling (any licence): download the CRM template below and upload it in Admin › Integrations › CRM › Add (Upload template). Lookup by: Number."),
			_("Turn on 'Call Journaling' in the CRM integration, and in the 3CX web client set 'Open contact on' to Ringing."),
			_("Map each agent's 3CX extension on the Agents tab."),
			_("Optional browser softphone: only if your 3CX exposes a WebRTC SIP gateway; otherwise agents use the 3CX app."),
		]

	def snippets(self):
		return [{
			"title": _("3CX CRM template (upload in Admin › Integrations › CRM)"),
			"language": "xml",
			"filename": f"neotec-3cx-{frappe.scrub(self.account.name)}.xml",
			"content": self._crm_template(),
		}]

	def _crm_template(self):
		def u(phase, extra=""):
			return (self.webhook_url(phase) + extra).replace("&", "&amp;")

		max_len = int(self.cfg("crm_number_length") or 9)
		lookup_url = u("lookup", "&number=[Number]")
		journal_url = u("journal", "&call_type=[CallType]&number=[Number]&agent=[Agent]&duration=[Duration]&start=[DateTime]&name=[Name]")
		outputs = "\n".join(
			f'        <Output Type="{t}" Passes="0" Value="[{v}]" />'
			for t, v in [("ContactID", "ContactID"), ("FirstName", "FirstName"), ("LastName", "LastName"),
				("CompanyName", "CompanyName"), ("PhoneBusiness", "PhoneBusiness"), ("ContactUrl", "ContactUrl"),
				("EntityId", "ContactID"), ("EntityType", "EntityType")]
		)
		variables = "\n".join(
			f'        <Variable Name="{n}" Path="contacts.{p}"><Filter /></Variable>'
			for n, p in [("ContactID", "id"), ("FirstName", "first_name"), ("LastName", "last_name"),
				("CompanyName", "company"), ("PhoneBusiness", "phone"), ("ContactUrl", "url"), ("EntityType", "entity_type")]
		)
		return f"""<?xml version="1.0"?>
<!-- Neotec Voice Hub · generated for account "{frappe.utils.escape_html(self.account.name)}".
     Validate in 3CX Admin before production; regenerate after rotating the webhook token. -->
<Crm xmlns="http://www.3cx.com/crm" Country="SA" Name="Neotec Voice Hub" Version="1" SupportsEmojis="true">
  <Number Prefix="AsIs" MaxLength="{max_len}" />
  <Connection MaxConcurrentRequests="16" />
  <Parameters>
    <Parameter Name="ReportCallEnabled" Type="Boolean" Editor="String" Title="Enable call journaling" Default="True" />
  </Parameters>
  <Authentication Type="No" />
  <Scenarios>
    <Scenario Type="REST">
      <Request SkipIf="" Url="{lookup_url}" MessagePasses="0" RequestEncoding="UrlEncoded" RequestType="Get" ResponseType="Json" />
      <Rules>
        <Rule Type="Any">contacts.id</Rule>
      </Rules>
      <Variables>
{variables}
      </Variables>
      <Outputs AllowEmpty="false">
{outputs}
      </Outputs>
    </Scenario>
    <Scenario Id="ReportCall" Type="REST">
      <Request SkipIf="[ReportCallEnabled]!=True" Url="{journal_url}" MessagePasses="0" RequestEncoding="UrlEncoded" RequestType="Post" ResponseType="Json" />
      <Variables />
      <Outputs AllowEmpty="true" />
    </Scenario>
  </Scenarios>
</Crm>
"""
