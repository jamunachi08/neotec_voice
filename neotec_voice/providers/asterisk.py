import frappe
from frappe import _

from neotec_voice.core.events import CallEvent
from neotec_voice.core.routing import dial_string
from neotec_voice.core.util import seconds
from neotec_voice.providers.base import F, SOFTPHONE_FIELDS, BaseProvider, ProviderError, WebhookResult, register

DISPOSITION = {"ANSWERED": "Completed", "ANSWER": "Completed", "NO ANSWER": "No Answer", "NOANSWER": "No Answer",
	"BUSY": "Busy", "FAILED": "Failed", "CONGESTION": "Failed", "CHANUNAVAIL": "Failed", "CANCEL": "Canceled"}
KIND = {"start": "initiated", "ring": "ringing", "answer": "answered", "hangup": "ended"}


@register
class AsteriskProvider(BaseProvider):
	vendor = "Asterisk"
	slug = "asterisk"
	label = "Asterisk / FreePBX / Issabel"
	category = "IP-PBX"
	color = "#F47B20"
	sort_order = 2
	tagline = "Run your own carrier-grade PBX"
	description = (
		"Neotec becomes the routing brain of your Asterisk: the dialplan asks Neotec who to ring, posts every event, "
		"and ARI originates click-to-call. Plug in STC / Mobily / Zain SIP trunks and you are the provider."
	)
	capabilities = frozenset({"outbound", "inbound", "events", "recording", "softphone", "hangup"})
	fields = [
		F("ari_url", "ARI Base URL", reqd=True, placeholder="https://pbx.example.sa:8089"),
		F("ari_user", "ARI Username", reqd=True),
		F("ari_password", "ARI Password", "Password", secret=True, reqd=True),
		F("endpoint_tech", "Agent channel technology", default="PJSIP", options="PJSIP\nSIP\nIAX2"),
		F("outbound_context", "Outbound dialplan context", default="nt-outbound"),
		F("trunk_dial_format", "Trunk dial format", default="PJSIP/{number}@trunk",
			description="Used for PSTN legs. {number} = E.164, {digits} = without +"),
		F("default_caller_id", "Default Caller ID"),
		F("recording_base_url", "Recording base URL (optional)", placeholder="https://pbx.example.sa/recordings/"),
		F("record_calls", "Record calls (MixMonitor)", "Check", default=1),
	] + SOFTPHONE_FIELDS

	def _ari(self, method, path, **kw):
		self.require("ari_url", "ari_user", "ari_password")
		return self.http(method, f"{self.cfg('ari_url').rstrip('/')}/ari/{path}",
			auth=(self.cfg("ari_user"), self.secret("ari_password")), **kw)

	def test_connection(self):
		info = self._ari("GET", "asterisk/info")
		ver = (info.get("system") or {}).get("version") or "?"
		return {"ok": True, "message": _("Connected to Asterisk {0}").format(ver)}

	def make_call(self, agent, to, caller_id, call_name):
		ext = agent.get("identity") or agent.get("extension")
		if not ext:
			raise ProviderError(_("Agent has no extension mapped for this PBX."))
		tech = self.cfg("endpoint_tech", "PJSIP")
		res = self._ari("POST", "channels", params={
			"endpoint": f"{tech}/{ext}",
			"extension": to,
			"context": self.cfg("outbound_context", "nt-outbound"),
			"priority": 1,
			"callerId": f'"Neotec → {to}" <{to}>',
			"timeout": 30,
		}, json={"variables": {"__NT_CALL": call_name, "__NT_CID": caller_id or self.cfg("default_caller_id") or "",
			"__NT_AGENT": ext}})
		return res.get("id")

	def hangup(self, call):
		if call.vendor_ref:
			self._ari("DELETE", f"channels/{call.vendor_ref}", expect_json=False)

	def _recording(self, path):
		if not path:
			return None
		if path.startswith("http"):
			return path
		base = self.cfg("recording_base_url")
		return f"{base.rstrip('/')}/{path.split('/')[-1]}" if base else None

	def handle_webhook(self, phase, p, request):
		ext_id = p.get("linkedid") or p.get("uniqueid")
		if phase == "route":
			target = self.route_inbound(p.get("to"), p.get("from"))
			tech = self.cfg("endpoint_tech", "PJSIP")
			body = dial_string(target, f"{tech}/{{identity}}", self.cfg("trunk_dial_format", "PJSIP/{number}@trunk"), "&")
			ev = CallEvent(external_id=ext_id, kind="ringing", direction="Inbound", from_number=p.get("from"),
				to_number=p.get("to"), ring_agents=target.users, queue=target.queue, route=target.route, raw=p)
			return WebhookResult([ev], body)
		if phase == "event":
			event = (p.get("event") or "").lower()
			kind = KIND.get(event, "status")
			status = None
			if event == "hangup":
				status = DISPOSITION.get((p.get("disposition") or "").upper(), "Completed" if seconds(p.get("billsec")) else "No Answer")
			ev = CallEvent(
				external_id=ext_id, local_ref=p.get("nt") or None, kind=kind, status=status,
				direction=(p.get("direction") or "").title() or None,
				from_number=p.get("from"), to_number=p.get("to"), agent_hint=p.get("agent") or None,
				duration=seconds(p.get("duration")), talk_time=seconds(p.get("billsec")),
				recording_url=self._recording(p.get("recording")), hangup_cause=p.get("disposition"),
				vendor_ref=p.get("uniqueid"), raw=p)
			return WebhookResult([ev], "OK")
		return WebhookResult()

	def webhooks(self):
		return [(_("Dialplan hook base (already embedded in the snippet)"), "event", "")]

	def setup_steps(self):
		return [
			_("Enable ARI: add the ari.conf user below and enable http.conf with TLS (port 8089). Reload."),
			_("Fill ARI URL/user/password here, Save and Test."),
			_("Paste the generated dialplan into extensions_custom.conf (FreePBX) or extensions.conf. Point your inbound trunk/DID to [nt-inbound] and route agent outbound calls to [nt-outbound]."),
			_("The dialplan calls Neotec for each inbound call (phase=route) — queues, schedules and presence are decided here, not on the PBX."),
			_("Map agent extensions on the Agents tab. For browser softphones, add the WebRTC endpoint template and fill WSS URL + SIP domain."),
			_("Set Recording base URL if recordings are served over HTTPS so Neotec can fetch them for transcripts."),
		]

	def snippets(self):
		hook = self.webhook_url("x").replace("&phase=x", "")
		tech = self.cfg("endpoint_tech", "PJSIP")
		trunk = self.cfg("trunk_dial_format", "PJSIP/{number}@trunk").replace("{number}", "${EXTEN}").replace("{digits}", "${EXTEN}")
		rec = "same => n,MixMonitor(${UNIQUEID}.wav,b)" if self.flag("record_calls", 1) else "same => n,NoOp(recording off)"
		dialplan = f"""; ===== Neotec Voice Hub — generated for "{self.account.name}" =====
; Review before production. Regenerate after rotating the webhook token.
[globals]
NTHOOK={hook}

[nt-notify] ; Gosub(nt-notify,s,1(event,from,to,direction))
exten => s,1,Set(CURLOPT(conntimeout)=3)
 same => n,Set(CURLOPT(httptimeout)=4)
 same => n,Set(NTR=${{CURL(${{NTHOOK}}&phase=event&event=${{ARG1}}&uniqueid=${{UNIQUEID}}&linkedid=${{CHANNEL(linkedid)}}&from=${{URIENCODE(${{ARG2}})}}&to=${{URIENCODE(${{ARG3}})}}&direction=${{ARG4}}&agent=${{URIENCODE(${{NT_AGENT}})}}&nt=${{NT_CALL}})}})
 same => n,Return()

[nt-hangup] ; hangup handler — posts the CDR
exten => s,1,Set(CURLOPT(conntimeout)=3)
 same => n,Set(NTR=${{CURL(${{NTHOOK}}&phase=event&event=hangup&uniqueid=${{UNIQUEID}}&linkedid=${{CHANNEL(linkedid)}}&from=${{URIENCODE(${{CALLERID(num)}})}}&to=${{URIENCODE(${{NT_TO}})}}&direction=${{NT_DIR}}&agent=${{URIENCODE(${{NT_AGENT}})}}&disposition=${{URIENCODE(${{DIALSTATUS}})}}&billsec=${{CDR(billsec)}}&duration=${{CDR(duration)}}&recording=${{URIENCODE(${{MIXMONITOR_FILENAME}})}}&nt=${{NT_CALL}})}})
 same => n,Return()

[nt-answered] ; runs on the agent channel when it answers (Dial U option)
exten => s,1,Set(NTC=${{CUT(CHANNEL,-,1)}})
 same => n,Set(NT_AGENT=${{CUT(NTC,/,2)}})
 same => n,Gosub(nt-notify,s,1(answer,${{CALLERID(num)}},${{NT_TO}},${{NT_DIR}}))
 same => n,Return()

[nt-inbound] ; point DIDs / inbound trunk here
exten => _X.,1,Set(__NT_DIR=inbound)
 same => n,Set(__NT_TO=${{EXTEN}})
 same => n,Set(CHANNEL(hangup_handler_push)=nt-hangup,s,1)
 same => n,Gosub(nt-notify,s,1(start,${{CALLERID(num)}},${{EXTEN}},inbound))
 same => n,Set(NTTARGET=${{CURL(${{NTHOOK}}&phase=route&uniqueid=${{UNIQUEID}}&linkedid=${{CHANNEL(linkedid)}}&from=${{URIENCODE(${{CALLERID(num)}})}}&to=${{URIENCODE(${{EXTEN}})}})}})
 same => n,GotoIf($["${{NTTARGET}}" = ""]?nobody)
 {rec}
 same => n,Dial(${{NTTARGET}},30,U(nt-answered^s^1))
 same => n(nobody),VoiceMail(1000@default,u) ; adjust fallback
 same => n,Hangup()

[nt-outbound] ; agents' outbound calls and ARI click-to-call land here
exten => _X.,1,Set(__NT_DIR=outbound)
 same => n,Set(__NT_TO=${{EXTEN}})
 same => n,ExecIf($["${{NT_AGENT}}" = ""]?Set(__NT_AGENT=${{CALLERID(num)}}))
 same => n,Set(NTH=${{PJSIP_HEADER(read,X-NT-Call)}})
 same => n,ExecIf($["${{NTH}}" != ""]?Set(__NT_CALL=${{NTH}}))
 same => n,Set(CHANNEL(hangup_handler_push)=nt-hangup,s,1)
 same => n,Gosub(nt-notify,s,1(start,${{NT_AGENT}},${{EXTEN}},outbound))
 same => n,ExecIf($["${{NT_CID}}" != ""]?Set(CALLERID(num)=${{NT_CID}}))
 {rec}
 same => n,Dial({trunk},60,U(nt-answered^s^1))
 same => n,Hangup()
"""
		ari = f"""; /etc/asterisk/ari.conf
[general]
enabled = yes
allowed_origins = {frappe.utils.get_url()}

[{self.cfg('ari_user', 'neotec')}]
type = user
read_only = no
password = <same password you entered in Neotec>
"""
		http = """; /etc/asterisk/http.conf
[general]
enabled = yes
bindaddr = 0.0.0.0
bindport = 8088
tlsenable = yes
tlsbindaddr = 0.0.0.0:8089
tlscertfile = /etc/asterisk/keys/fullchain.pem
tlsprivatekey = /etc/asterisk/keys/privkey.pem
"""
		webrtc = f"""; pjsip.conf — WebRTC agent template (one per agent, e.g. 1001)
[nt-webrtc](!)
type = endpoint
transport = transport-wss
context = nt-outbound
disallow = all
allow = opus,ulaw
webrtc = yes
dtls_auto_generate_cert = yes

[1001](nt-webrtc)
auth = 1001
aors = 1001

[1001]
type = auth
auth_type = userpass
username = 1001
password = <agent SIP password — also set on the Voice Agent>

[1001]
type = aor
max_contacts = 3
remove_existing = yes
; Agents channel tech used by Neotec: {tech}
"""
		return [
			{"title": _("Dialplan (extensions.conf / extensions_custom.conf)"), "language": "ini", "filename": "extensions_neotec.conf", "content": dialplan},
			{"title": _("ARI user (ari.conf)"), "language": "ini", "filename": "ari.conf", "content": ari},
			{"title": _("HTTP/TLS for ARI & WebRTC (http.conf)"), "language": "ini", "filename": "http.conf", "content": http},
			{"title": _("WebRTC endpoint template (pjsip.conf)"), "language": "ini", "filename": "pjsip_neotec_webrtc.conf", "content": webrtc},
		]
