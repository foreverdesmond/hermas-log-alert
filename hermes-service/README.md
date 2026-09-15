# Hermes-side receiver (log alert)

The server half of the pipeline. It verifies the collector's signed events, decides
which ones deserve a real-time notification, and produces the daily digest.

It runs inside its own Hermes profile: separate gateway process, separate bot, separate
state. It never opens its own public port — the public entry is **one exact path** on an
existing HTTPS host, reverse-proxied to a loopback listener, and the health endpoint
stays loopback-only.

## Components

| Component | Path | Role |
|---|---|---|
| route subscription | `config/webhook-subscription.sample.json` | the webhook route: signing secret, delivery target, prompt, and the pre-LLM gate script. Re-read on every request, so edits need no restart |
| pre-LLM gate | `scripts/log_storm_guard.py` | executed as a subprocess for **every** request. Severity routing, body-level authority, storm aggregation, per-class cooldown. Prints `[SILENT]` or an enriched JSON payload |
| daily digest collector | `scripts/log_storm_daily_digest.py` | cron `script`: compresses the window's collected events into clusters; its stdout is injected into the agent prompt |
| trial review collector | `scripts/log_storm_trial_review.py` | one-off, read-only observability pass over an observation window |
| prompts | `config/prompts/*.md` | verbatim prompt text for the triage route and both cron jobs |
| runtime state | `scripts/log_storm_state.sqlite3`, `scripts/log_storm_digest_state.json`, `scripts/log_storm_guard_errors.log` | audit store, digest watermark, gate-failure breadcrumb (created at runtime, mode `600`) |

## Data flow

```
collector ──signed HTTPS──▶ public exact path ──▶ loopback webhook listener
                                                      │
                          signature check ────────────┤  401 on failure
                                                      ▼
                          pre-LLM gate (per-request subprocess)
                                                      │
                        [SILENT] ────────────────────┤  records for the digest only
                                                      ▼
                          LLM triage (Chinese, fixed templates) ──▶ notification
                                                      │
                          daily cron: digest collector ──▶ LLM digest ──▶ notification
```

The log body is always treated as **untrusted data**: the prompts forbid following
commands found in it, visiting URLs found in it, or changing configuration because of
it.

## Routing rules (all before the LLM)

| Case | Action |
|---|---|
| `warning`, `info`, `debug`, `trace`, `notice` | recorded only, never wakes the LLM — they go to the daily digest |
| level written in the log line disagrees with the collector's label | the **line's own level wins**, in both directions: an `INFO`/`WARN` line mislabelled as `error` is collected, an `ERROR` line labelled `warning` is escalated to real time |
| line contains a real fault word (`failed`, `exception`, `timeout`, …) | **never downgraded** (prefer a false alarm over a silent miss) |
| `error` | real time, but through a **per-class cooldown**: same host + source + level + component + message-head ⇒ at most one page per hour; 5 in 300 s ⇒ immediate page; every suppressed event is still recorded |
| `critical` | **always real time**, exempt from every cooldown |
| state store unreadable | fail closed: `[SILENT]` plus a breadcrumb, reported by the next digest's watchdog block |

Safety property: a class seen for the **first time always goes through** — aggregation
only compresses repeats, it can never hide a new fault. Verify with an A/B replay of the
real inbound stream ("every class the old version paged, the new version still pages").

## Install

Placeholders: `<PROFILE_DIR>` = the Hermes profile directory, `<GATEWAY_HOST>` = the
existing HTTPS host, `<TARGET_CHAT_ID>` = the bot chat, `<WEBHOOK_SIGNING_SECRET>` = the
shared signing secret (kept in local secrets only).

1. Create a dedicated profile with its own bot; keep the public surface at one path.
2. Copy `scripts/*.py` into `<PROFILE_DIR>/scripts/` and keep them mode `700`.
   They are executed per request — **no service restart is needed after an edit**.
3. Create `<PROFILE_DIR>/webhook_subscriptions.json` from
   `config/webhook-subscription.sample.json`: fill in the secret and the chat id, and
   paste `config/prompts/alert-triage.md` verbatim into the `prompt` field. This file is
   hot-loaded per request.
4. Create the cron jobs: the daily digest (`script` = `log_storm_daily_digest.py`,
   prompt = `config/prompts/daily-digest.md`, once a day in local time) and, if wanted,
   the one-off trial review (`config/prompts/trial-review.md`).
5. Reverse-proxy `<GATEWAY_HOST>` so that the exact webhook path reaches the loopback
   listener, POST-only, with basic auth disabled **for that path only**. Everything else
   on that host stays untouched.
6. Run the verification checklist below.

## Verification checklist

- gateway process active; listener bound to **loopback only**
- health endpoint `200`; missing header / wrong signature `401`
- a valid synthetic event (real HMAC, signed locally) → `202`, and the delivery record
  shows `delivered`, `attempts=0`, no error
- a `warning` event is fully silent and never dispatched to the LLM
- the same `error` class at different timestamps aggregates into one class
- state store missing/corrupt → exit code 0, `[SILENT]`, plus a breadcrumb (never a crash)
- invalid input (non-JSON, JSON array) → `[SILENT]`
- digest collector runs on a real snapshot, the watermark suppresses an immediate second
  run, and both watchdog branches can be triggered
- any basic-auth-protected path on the same host is still `401`

## Operational discipline

1. **Single writer.** Only one operator edits the server side; the collector is owned
   separately. Two writers with an older copy in hand silently revert each other's fixes.
2. Every change: back up (with hash), edit in an isolated copy, test there, syntax-check,
   install, re-hash, keep the original file mode, fire a real event, then log the change
   with a rollback command and bump the version constant.
3. Prefer not to restart anything: the gate runs per request and the subscription is
   hot-loaded. Back up the audit store before any schema change.
4. Secrets never enter the audit store, the repository, the prompts, or the logs.
5. **A failed digest run is not a missing watermark advance.** The collector writes the
   watermark before the LLM step, so an upstream error leaves "collected but never
   delivered"; that window is not retried automatically. To backfill, back up the
   watermark, move it back past the gap, run once, then restore it.

## Version history

| Version | Change |
|---|---|
| `log_storm_guard.py` 1.6.0 | group keys normalise the `log_file` rotation part (a date-stamped file name used to reset every per-class cooldown at midnight) |
| 1.5.0 | per-class cooldown aggregation for `error` |
| 1.4.0 | the level written inside the log line becomes authoritative (both directions) |
| 1.3.0 | state-store failures fail closed with a breadcrumb; fingerprint collapses timestamps |
| 1.2.0 | `warning` becomes digest-only; `critical`/`error` keep the real-time path |
| 1.1.0 | fingerprint normalisation, storm aggregation, silence on state-store errors |
| 1.0.0 | initial gate |
| `log_storm_daily_digest.py` 1.5.0 | clusters carry the collector's authoritative `program` / `project` / `log_file`, plus the component tag found in the line |
| 1.4.0 | deterministic component extraction so every digest item names a program |
| 1.3.0 | digest watermark, watchdog block |
| 1.0.0 | initial collector |
