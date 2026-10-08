import json
import socket
from urllib.parse import unquote

from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.routing import dial_string
from neotec_voice.core.util import seconds
from neotec_voice.providers.base import F, SOFTPHONE_FIELDS, BaseProvider, ProviderError, WebhookResult, register

CAUSE = {"NO_ANSWER": "No Answer", "NO_USER_RESPONSE": "No Answer", "USER_BUSY": "Busy",
	"ORIGINATOR_CANCEL": "Canceled", "CALL_REJECTED": "Busy"}


@register
class FreeSwitchProvider(BaseProvider):
	vendor = "FreeSWITCH"
	slug = "freeswitch"
	label = "FreeSWITCH / FusionPBX"
	category = "IP-PBX"
	color = "#2F855A"
	sort_order = 3
	tagline = "Scale-out softswitch under your control"
	description = "ESL originate for click-to-call, mod_curl routing decided by Neotec, mod_json_cdr for CDRs and Verto/SIP-WSS softphones."
	capabilities = frozenset({"outbound", "inbound", "events", "recording", "softphone", "hangup"})
	fields = [
		F("esl_host", "ESL Host", reqd=True, placeholder="10.0.0.5"),
		F("esl_port", "ESL Port", "Int", default=8021),
		F("esl_password", "ESL Password", "Password", secret=True, reqd=True),
		F("user_domain", "User directory domain", reqd=True, placeholder="pbx.example.sa"),
		F("gateway", "Sofia gateway for PSTN", reqd=True, placeholder="stc-trunk"),
		F("default_caller_id", "Default Caller ID"),
		F("recording_base_url", "Recording base URL (optional)"),
	] + SOFTPHONE_FIELDS

	def _esl(self, command):
		self.require("esl_host", "esl_password")
		try:
			sock = socket.create_connection((self.cfg("esl_host"), int(self.cfg("esl_port", 8021))), timeout=8)
		except OSError as e:
			raise ProviderError(_("ESL connection failed: {0}").format(e)) from e
		with sock:
			stream = sock.makefile("rb")

			def read():
				headers = {}
				while True:
					line = stream.readline()
					if not line:
						raise ProviderError(_("ESL connection closed"))
					line = line.decode(errors="replace").strip()
					if not line:
						if headers:
							break
						continue
					key, _sep, value = line.partition(":")
					headers[key.strip()] = value.strip()
				size = int(headers.get("Content-Length") or 0)
				body = stream.read(size).decode(errors="replace") if size else ""
				return headers, body

			read()
			sock.sendall(f"auth {self.secret('esl_password')}\n\n".encode())
			headers, _body = read()
			if not headers.get("Reply-Text", "").startswith("+OK"):
				raise ProviderError(_("ESL authentication failed"))
			sock.sendall(f"api {command}\n\n".encode())
			_h, body = read()
			return body.strip()

	def test_connection(self):
		out = self._esl("status")
		return {"ok": "UP" in out, "message": out.splitlines()[0] if out else _("No response")}

	def make_call(self, agent, to, caller_id, call_name):
		ext = agent.get("identity") or agent.get("extension")
		if not ext:
			raise ProviderError(_("Agent has no extension mapped for this switch."))
		cid = caller_id or self.cfg("default_caller_id") or ""
		dom, gw = self.cfg("user_domain"), self.cfg("gateway")
		cmd = (f"originate {{nt_call={call_name},nt_dir=outbound,nt_agent={ext},origination_caller_id_number={to},"
			f"ignore_early_media=true}}user/{ext}@{dom} "
			f"&bridge([origination_caller_id_number={cid}]sofia/gateway/{gw}/{to})")
		out = self._esl(cmd)
		if not out.startswith("+OK"):
			raise ProviderError(out or _("Originate failed"))
		return out.split(" ", 1)[1].strip() if " " in out else None

	def hangup(self, call):
		if call.external_id:
			self._esl(f"uuid_kill {call.external_id}")

	def handle_webhook(self, phase, p, request):
		if phase == "route":
			target = self.route_inbound(p.get("to"), p.get("from"))
			body = dial_string(target, f"user/{{identity}}@{self.cfg('user_domain')}",
				f"sofia/gateway/{self.cfg('gateway')}/{{number}}", ",")
			ev = CallEvent(external_id=p.get("uuid"), kind="ringing", direction="Inbound", from_number=p.get("from"),
				to_number=p.get("to"), ring_agents=target.users, queue=target.queue, route=target.route, raw=p)
			return WebhookResult([ev], body or "error/USER_NOT_REGISTERED")
		if phase == "event":
			kind = {"answer": "answered", "ring": "ringing"}.get((p.get("event") or "").lower(), "status")
			ev = CallEvent(external_id=p.get("uuid"), local_ref=p.get("nt") or None, kind=kind,
				agent_hint=p.get("agent") or None, raw=p)
			return WebhookResult([ev], "OK")
		if phase == "cdr":
			return WebhookResult([self._cdr(p)], "OK")
		return WebhookResult()

	def _cdr(self, p):
		data = p.get("cdr")
		data = json.loads(data) if isinstance(data, str) else (data or p)
		v = {k: unquote(val) if isinstance(val, str) else val for k, val in (data.get("variables") or {}).items()}
		flow = data.get("callflow") or [{}]
		prof = (flow[0] if isinstance(flow, list) and flow else flow).get("caller_profile") or {}
		billsec = seconds(v.get("billsec")) or 0
		cause = v.get("hangup_cause") or ""
		status = "Completed" if billsec > 0 else CAUSE.get(cause, "Failed" if cause and cause != "NORMAL_CLEARING" else "No Answer")
		rec = v.get("nt_recording") or v.get("record_path")
		if rec and not rec.startswith("http") and self.cfg("recording_base_url"):
			rec = f"{self.cfg('recording_base_url').rstrip('/')}/{rec.split('/')[-1]}"
		direction = v.get("nt_dir") or ("inbound" if v.get("direction") == "inbound" else "outbound")
		return CallEvent(
			external_id=v.get("nt_uuid") or v.get("uuid"), local_ref=v.get("nt_call") or None, kind="ended",
			status=status, direction=direction.title(),
			from_number=prof.get("caller_id_number") or v.get("caller_id_number"),
			to_number=v.get("nt_did") or prof.get("destination_number") or v.get("destination_number"),
			agent_hint=v.get("nt_agent") or v.get("callee_id_number"),
			duration=seconds(v.get("duration")), talk_time=billsec, started_at=v.get("start_epoch"),
			answered_at=v.get("answer_epoch") if billsec else None, ended_at=v.get("end_epoch"),
			hangup_cause=cause, recording_url=rec if rec and rec.startswith("http") else None, raw=data)

	def webhooks(self):
		return [(_("JSON CDR endpoint (mod_json_cdr)"), "cdr", ""), (_("Routing (mod_curl, embedded in dialplan)"), "route", "")]

	def setup_steps(self):
		return [
			_("Allow ESL from the Frappe server in event_socket.conf.xml (listen-ip, apply-inbound-acl) and set a strong password."),
			_("Fill ESL host/port/password, user domain and your PSTN gateway name. Save and Test."),
			_("Load mod_curl and mod_json_cdr; paste json_cdr.conf.xml below and reloadxml."),
			_("Add the inbound dialplan extension to your public context — Neotec returns the bridge string per call."),
			_("Map agent extensions on the Agents tab. For browser softphones, enable WSS on the internal profile and fill WSS URL + SIP domain."),
		]

	def snippets(self):
		esc = lambda s: s.replace("&", "&amp;")
		route = esc(self.webhook_url("route"))
		answer = esc(self.webhook_url("event", event="answer"))
		cdr = esc(self.webhook_url("cdr"))
		dialplan = f"""<!-- Neotec Voice Hub — inbound (public context). Review before production. -->
<extension name="neotec_inbound">
  <condition field="destination_number" expression="^(\\+?\\d{{6,15}})$">
    <action application="export" data="nt_dir=inbound"/>
    <action application="export" data="nt_did=$1"/>
    <action application="set" data="nt_target=${{curl({route}&amp;uuid=${{uuid}}&amp;from=${{caller_id_number}}&amp;to=$1)}}"/>
    <action application="set" data="api_on_answer=curl {answer}&amp;uuid=${{uuid}}"/>
    <action application="set" data="nt_recording=$${{recordings_dir}}/${{uuid}}.wav"/>
    <action application="record_session" data="${{nt_recording}}"/>
    <action application="set" data="call_timeout=30"/>
    <action application="set" data="continue_on_fail=true"/>
    <action application="set" data="hangup_after_bridge=true"/>
    <action application="bridge" data="${{nt_target}}"/>
    <action application="answer"/>
    <action application="voicemail" data="default ${{domain_name}} 1000"/>
  </condition>
</extension>
"""
		json_cdr = f"""<configuration name="json_cdr.conf" description="Neotec JSON CDR">
  <settings>
    <param name="url" value="{cdr}"/>
    <param name="encode-values" value="true"/>
    <param name="log-b-leg" value="false"/>
    <param name="prefix-a-leg" value="false"/>
    <param name="retries" value="2"/>
    <param name="delay" value="5"/>
    <param name="log-http-and-disk" value="false"/>
    <param name="err-log-dir" value="$${{log_dir}}/json_cdr"/>
  </settings>
</configuration>
"""
		return [
			{"title": _("Inbound dialplan extension"), "language": "xml", "filename": "neotec_inbound.xml", "content": dialplan},
			{"title": _("autoload_configs/json_cdr.conf.xml"), "language": "xml", "filename": "json_cdr.conf.xml", "content": json_cdr},
		]
