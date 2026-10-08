# Architecture

```
            ┌───────────── Frappe / ERPNext (any app) ─────────────┐
            │  Lead · Customer · Contact · Ticket · Employee ...   │
            │        ▲ timeline        ▲ Call buttons, screen-pop  │
            └────────┼─────────────────┼───────────────────────────┘
                     │                 │  neotec_voice.api.v1 (vendor-neutral)
┌────────────────────┴─────────────────┴──────────────────────────────────────┐
│ Neotec Voice core                                                           │
│  routing.py   numbers → time routes → queues → presence → Target            │
│  outbound.py  least-cost route → carrier 1 → (fails) → carrier 2 ...        │
│  events.py    CallEvent → idempotent, monotonic lifecycle → side effects    │
│  contacts.py  caller identification across configured DocTypes             │
│  rating.py    longest-prefix buy/sell rating      billing.py  ERPNext SI    │
│  intelligence.py  recording copy → STT → summary (self-hosted, allow-list)  │
└───────────────▲──────────────────────────────────────▲──────────────────────┘
                │ Target (who to ring)                 │ CallEvent (what happened)
┌───────────────┴──────────────────────────────────────┴──────────────────────┐
│ Providers (one class per vendor, registry + hooks)                          │
│  render Target as: TwiML · NCCO · Plivo XML · Exotel numbers · Telnyx        │
│  actions · Asterisk dial string · FreeSWITCH bridge · JSON/text             │
│  parse payloads into CallEvent; make_call / send_sms / hangup / provision   │
└───────────────▲─────────────────────────────────────────────────────────────┘
                │ /api/method/neotec_voice.api.webhook.receive?k=…&t=…&phase=…
        3CX · Asterisk · FreeSWITCH · Yeastar · Twilio · Exotel · Vonage · Plivo · Telnyx · any HTTP PBX
```

## Key decisions

**One gateway, many phases.** All vendors post to one method; `k` selects the account, `t` authenticates,
`phase` selects the handler (`voice`, `status`, `route`, `cdr`, `lookup`, `journal`, ...). Vendors that need an
answer (TwiML, NCCO, XML, dial string, JSON) get it in the same response.

**Routing lives in Neotec, not in the vendor.** Inbound calls ask Neotec who to ring. Queues, schedules, overflow,
presence and voicemail are configured once and work identically on Twilio, Asterisk or Telnyx. This is what turns a
deployment into a provider: SIP trunks from any Saudi operator into Asterisk/FreeSWITCH, Neotec on top, and the
same experience as a cloud CPaaS — with data kept in-Kingdom.

**Normalised events.** Providers emit `CallEvent`s. `ingest()` dedupes by content hash, resolves calls by our
reference (`nt`) or the vendor id, never lets a call move backwards (a late "ringing" can't reopen a completed
call), treats unanswered inbound as *Missed*, and derives timing, wait time, talk time and cost. Concurrent first
events for the same call are handled with a unique key and a savepoint retry.

**Our id travels with the call.** Outbound calls are created first and their name is passed to the vendor (URL
param, client_state, channel variable, SIP header `X-NT-Call`). Events echo it back, so links to the originating
document are never lost even before the vendor id is known.

**Secrets.** Non-secret config is JSON on the account; secrets go to Frappe's encrypted password store keyed
`nts_<field>`.

**Extensibility.** `neotec_voice_providers` hook registers external `BaseProvider` subclasses; each declares its
fields, capabilities, webhook phases, vendor-side steps and generated snippets, and gets a setup experience for free
(add a Page whose JS calls `neotec.voice.vendor_page(wrapper, "<vendor>")`).

## Status model

`Initiated → Queued → Ringing → In Progress/On Hold → {Completed, Busy, No Answer, Missed, Voicemail, Failed, Canceled}`
Terminal states are final. Agent presence: `Available → On Call → Wrap Up (auto-release) → Available`; `Away`, `Offline` manual.

## Known limits (verify per site)

- 3CX Call Control needs an 8SC+ Enterprise licence; the CRM template path (lookup + journaling) works on any licence.
  Validate the generated template in the 3CX console before production.
- Yeastar event codes and payloads vary by firmware; the parser is defensive but check your build's API guide.
- Vonage `connect` rings one endpoint per action — Ring-All queues ring the first available agent.
- Generated Asterisk/FreeSWITCH config is a starting point: review contexts, trunk names and recording paths.
