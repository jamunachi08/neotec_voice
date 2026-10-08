# Neotec Voice Hub

Vendor-neutral voice, contact-centre and voice-reseller platform for Frappe / ERPNext v15.
Neotec Integrated Solutions.

Neotec does not just *forward* calls from a carrier. It is the routing brain, the system of record,
the rating engine and the billing system; carriers and PBXs plug in underneath it. Swap or add a
carrier without touching a single queue, route, report or integration.

## Connectors (one setup page each)

| Connector | Type | Click-to-call | Neotec routes inbound | Events | Softphone | SMS |
|---|---|---|---|---|---|---|
| 3CX v20 | IP-PBX | Call Control API | via 3CX (screen-pop + journaling by generated CRM template) | ✓ | 3CX app | |
| Asterisk / FreePBX / Issabel | IP-PBX | ARI | ✓ dialplan asks Neotec | ✓ | ✓ WebRTC | |
| FreeSWITCH / FusionPBX | IP-PBX | ESL | ✓ mod_curl | ✓ json_cdr | ✓ WebRTC | |
| Yeastar P-Series | IP-PBX | Open API | via Yeastar | ✓ | Linkus | |
| Twilio | Cloud | ✓ | ✓ TwiML | ✓ signed | ✓ Voice SDK | ✓ |
| Exotel | Cloud | ✓ | ✓ Connect applet | ✓ | | ✓ |
| Vonage | Cloud | ✓ | ✓ NCCO | ✓ | | ✓ |
| Plivo | Cloud | ✓ | ✓ Plivo XML | ✓ | | ✓ |
| Telnyx | Cloud | ✓ | ✓ Call Control | ✓ signed | | ✓ |
| Unifonic | SMS (KSA) | | | | | ✓ |
| Taqnyat | SMS (KSA) | | | | | ✓ |
| Open Connector | Any HTTP PBX/carrier | template | ✓ text/JSON | ✓ mapped | ✓ SIP | |

Other apps add connectors without forking: `neotec_voice_providers = ["my_app.voice.MyProvider"]` in their `hooks.py`.

## Install

```bash
bench get-app https://github.com/jamunachi08/neotec_voice
bench --site <site> install-app neotec_voice
bench --site <site> migrate
python3 apps/neotec_voice/verify_tree.py
bench --site <site> run-tests --app neotec_voice
```

On Frappe Cloud: push to the connected branch; migrate runs on deploy. No extra Python packages are needed.

## First call in ten minutes

1. **Voice Settings** — default country (SA), voicemail prompts, caller-lookup sources.
2. **Voice Agent** for each user who takes calls (device, extension, mobile).
3. **Voice Hub → pick a connector** → Credentials → Save → Test.
4. **Connect the vendor** tab — copy the addresses / download the generated config.
5. **Map agents** and **Numbers**; add a **Voice Queue** and a **Voice Route** if you want schedules.
6. Open any Lead/Customer — the *Call* button is there; calls appear on its timeline.

## Public API (vendor-neutral)

`neotec_voice.api.v1.*` — `call`, `prepare_call`, `hangup`, `me`, `set_status`, `heartbeat`, `lookup`,
`save_wrap_up`, `get_call`, `recent_calls`, `softphone_config`, `send_sms`, `otp_send`, `otp_verify`, `live`, `kpis`.

## Security

- Every webhook address carries a per-account key and a 40-char token (constant-time compared); rotate from the setup page.
- Twilio (HMAC-SHA1) and Telnyx (Ed25519) signatures verified on top.
- Vendor secrets live in Frappe's encrypted password store, never in config JSON.
- AI post-processing only talks to hosts on the residency allow-list (PDPL).
- OTP codes stored as PBKDF2 hashes; uniform failures; resends never reset the attempt budget.

See `ARCHITECTURE.md` for the design.
