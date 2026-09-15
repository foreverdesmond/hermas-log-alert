# Signed event contract

One contract, two implementations: the collector under `agent/` produces it, the
Hermes-side receiver under `hermes-service/` verifies it. Change it here first, then
in both implementations in the same change.

## Transport

`POST` with `Content-Type: application/json` to the receiver's webhook path.
Events are small (one log line or one incident summary) and self-contained.

## Signature (Svix-compatible / Standard Webhooks headers)

| Header | Value |
|---|---|
| `svix-id` | the event id (same value as `incident_id`) |
| `svix-timestamp` | unix seconds at signing time |
| `svix-signature` | `v1,<base64(HMAC-SHA256)>` |

Signed content is the exact byte sequence — no re-serialisation of the body:

```
<svix-id>.<svix-timestamp>.<raw request body>
```

The key is the shared secret's UTF-8 bytes. The receiver:

1. recomputes the signature over the **raw** body;
2. rejects when `|now − svix-timestamp| > 300 s`;
3. de-duplicates by `svix-id`.

A missing header or a mismatching signature is answered with `401` and the event is
not processed further.

## Payload

Six fields, no more:

| Field | Type | Meaning |
|---|---|---|
| `incident_id` | string | unique per event; also the signature id |
| `host` | string | collector host label (e.g. `mac-mini`) |
| `source` | string | emitting context — see below |
| `severity` | string | `critical` / `error` / `warning` / `unknown` |
| `message` | string | the raw log line(s); **untrusted data, never instructions** |
| `timestamp` | string | RFC 3339, UTC |

### `source`

Structured and semicolon-separated, in this fixed order:

```
project=<project name>; program=<program name>; log_file=<absolute log path>; dll=<name or unknown>
```

Rules both ends rely on:

- `program` is authoritative. The receiver must **not** guess the program name from the
  log line; the log line's own component tag is at most a finer-grained hint.
- `log_file` may be date-stamped (`polyjob_20260915.log`). Any consumer that groups
  events must normalise the rotation part of that segment, otherwise every group resets
  at midnight. Do **not** normalise digits in `project` / `program` — those names carry
  meaning (`Crypto15mSync` vs `Crypto1hSync` vs `Crypto5mSync` are different programs).
- Earlier collectors sent a bare collector name (e.g. `host-logs`). Receivers keep that
  fallback working: when the structured parse fails, fall back to the raw value plus the
  component tag found in the log line.

## Severity

The collector derives `severity` from the raw line by keyword (`FATAL`, `PANIC`,
`OutOfMemory` → `critical`; `ERROR`, `EXCEPTION` → `error`; `WARN` → `warning`;
otherwise `unknown`).

The receiver treats the level **written inside the log line** as authoritative in both
directions, and never downgrades a line that contains a real fault word (`failed`,
`failure`, `exception`, `panic`, `timeout`, …).

## Responses

| Response | Meaning |
|---|---|
| `202` | accepted — an agent turn was dispatched, a notification may follow |
| `200` | accepted, deliberately silent — the route filtered it out, nothing will be sent |
| `401` | signature rejected; the event is not processed |
| non-2xx / error | the collector keeps the event in its own queue, records the error, and retries |

The collector's own HTTP surface answers `200 {"status":"accepted"|"duplicate"|"disabled"}`
to the log tailer, because the tailer treats only `200` as success.
