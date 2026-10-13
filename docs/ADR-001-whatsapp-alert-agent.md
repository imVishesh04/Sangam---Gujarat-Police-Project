# ADR-001: WhatsApp Alert Agent

**Status:** Accepted
**Date:** 11 Oct 2026
**Deciders:** Team Root Access (Vishesh Satapathy, Vishal Raj)

## Context

Officers are not always in front of the SANGAM dashboard. A watchlist match must
reach them on the phone they already carry, with enough detail to act: plate,
last location, map link, recent sightings and registration details.

Constraints: single-laptop prototype (Flask + eventlet + SQLite), demo the next
day, venue internet not guaranteed, and alert data is police-sensitive.

## Decision

Add a background **alert agent** that the matching engine hands every new alert to.
The agent enriches the alert (camera location, recent sightings, RTO decode,
registry record), reasons over it (cloned-plate check, auto priority, dedupe,
flood control) and sends a WhatsApp message through a pluggable provider.

- Provider: **Twilio WhatsApp API** for the prototype (sandbox, no business
  verification), **Meta WhatsApp Cloud API** supported as the production path,
  **log** provider for offline demos.
- Registry: plate decode (state + RTO office) is always on. Owner/vehicle data
  comes through a **VAHAN adapter** that needs authorised access; the prototype
  uses a clearly labelled SAMPLE registry for fictional demo plates only.
- Dispatch: in-process queue + worker thread (non-blocking for ANPR).
- Reasoning is **rule-based**, not generative: every line must trace to stored data.

## Options Considered

### WhatsApp channel

| Option | Setup time | Cost | Reliability | Fit |
|---|---|---|---|---|
| Twilio WhatsApp (sandbox) | ~10 min | Free trial | High | Prototype |
| Meta WhatsApp Cloud API | 1-2 h + templates for cold messages | Free tier | High | Production |
| Unofficial (CallMeBot, WhatsApp Web automation) | Low | Free | Low, may violate WhatsApp terms | Rejected |

### Registry enrichment

| Option | Verdict |
|---|---|
| Scrape public "RC lookup" websites | Rejected - unauthorised, personal data (DPDP Act) |
| Invent records for any plate | Rejected - fabricated records about real vehicles |
| Plate decode + authorised VAHAN adapter + labelled sample data | **Chosen** |

### Dispatch

| Option | Verdict |
|---|---|
| Send inside the matching engine (synchronous) | Rejected - a slow network would stall ANPR |
| Celery/Redis queue | Right at scale; too heavy for tomorrow |
| In-process queue + worker thread | **Chosen** for the prototype |

## Trade-off Analysis

The in-process queue loses queued messages if the process crashes. That is acceptable
for a demo; production moves the queue to Redis/Celery with persistent retries.
Twilio's sandbox only delivers to numbers that joined it, which is good for a
controlled demo but not for rollout - Meta Cloud API with an approved alert
template is the production channel. Rule-based reasoning is less flexible than
an LLM, but it can never invent a location or plate in a police alert.

## Consequences

- Easier: officers get actionable alerts away from the dashboard; every send,
  failure and skip is in the audit log.
- Harder: secrets management (Twilio/Meta tokens live only in `.env`).
- PII: owner names masked; phone, address, chassis and engine numbers never sent.
- Revisit: two-way commands ("track GJ01AB1234") via an inbound webhook;
  VAHAN integration once authorised; Redis queue for multi-process deployment.

## Action Items

1. [x] `alert_agent.py`, `whatsapp.py`, `rto_lookup.py`, `/notifications/*` endpoints
2. [x] Dashboard card (System Health) with test buttons and last-message preview
3. [x] Tests with mocked HTTP; test suite forced to the log provider
4. [ ] Production: Meta template approval, VAHAN authorisation, Redis queue
