# Changelog

## 1.0.1 — 2026-10-08
- Released under the Neotec brand: app `neotec_voice`, module Neotec Voice, title Neotec Voice Hub. Webhook paths, JS namespace (`neotec.voice`), provider hook (`neotec_voice_providers`), dialplan contexts (`nt-*`) and SIP header (`X-NT-Call`) renamed accordingly.
- Removed a stray empty directory from the package.
- `verify_tree.py` now fails if any previous brand name remains.

## 1.0.0 — 2026-10-08
First release of Neotec Voice Hub.

- Provider framework with 12 connectors: 3CX, Asterisk/FreePBX, FreeSWITCH/FusionPBX, Yeastar P-Series, Twilio, Exotel, Vonage, Plivo, Telnyx, Unifonic, Taqnyat and an Open Connector for any HTTP-capable PBX or carrier. Other apps can register more through the `neotec_voice_providers` hook.
- One setup page per vendor (credentials, vendor-side steps, generated configuration, agent mapping, numbers, activity) plus the Voice Hub and the live Voice Console.
- Multiple accounts per vendor; credentials stored encrypted; per-account webhook key and token, with Twilio and Telnyx signature verification on top.
- Neotec routing brain: numbers, time-of-day routes (overnight windows, Sun–Thu default), queues with Ring All / Round Robin / Longest Idle / Fewest Calls, overflow and bilingual voicemail — rendered as TwiML, NCCO, Plivo XML, Exotel dynamic numbers, Telnyx Call Control actions, Asterisk dial strings and FreeSWITCH bridge strings.
- Outbound least-cost routing with automatic carrier failover.
- Normalised call lifecycle: idempotent events, monotonic status, agent presence and wrap-up, missed-call detection, caller identification across Contact/Lead/Customer/Supplier/Employee.
- Screen-pop cards, wrap-up with outcome and follow-up ToDo, call buttons on every form with phone fields, calls on every linked document's timeline.
- Browser softphone for SIP-over-WSS PBXs and Twilio.
- Rating engine (longest-prefix, increments, minimums, connection fee) and monthly reseller invoicing (usage + number rental) into ERPNext.
- Post-call recording copy, transcription and summary on self-hosted models only, enforced by a host allow-list; recording retention job.
- SMS with failover across gateways; OTP over SMS/Email with hashed codes and uniform failure responses.
- Arabic translations for agent-facing UI.
