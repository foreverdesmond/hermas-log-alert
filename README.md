# Hermas Log Alert

Lightweight, AI-assisted log alert pipeline. The repository keeps the Docker
collector and the Hermes receiver together so their signed webhook contract
evolves in one place. It supports Linux Docker hosts, macOS Docker Desktop,
and Windows Docker Desktop running Linux containers; the images are built
natively for the architecture of the build host.

## Layout

- `agent/` — Docker log collector, local filtering, deduplication, and Hermes forwarding.
- `hermes-service/` — server side: pre-LLM gate, AI triage, notification, daily digest
  (see [`hermes-service/README.md`](hermes-service/README.md)).
- `protocol/` — the shared signed-event contract both ends implement
  (see [`protocol/README.md`](protocol/README.md)).
- `deploy/` — deployment notes and environment-specific assets.

## Secrets and runtime data

Copy `agent/.env.sample` to the untracked visible file `agent/config.env`, fill
in the values, and enter those variables in Portainer when deploying. Do not
commit that file. The `.gitignore` also excludes database files and log files.

On the receiver side, create the route subscription from
`hermes-service/config/webhook-subscription.sample.json`, and paste the prompt from
`hermes-service/config/prompts/alert-triage.md` into it verbatim. The signing secret and
the notification chat id stay in local secrets and are never committed.

`agent/projects.json` is safe to commit when it contains only project and
program names. Use `agent/projects.sample.json` as the generic starting point.

## Third-party software

The collector builds on Logtail and Fwatch under Apache-2.0. Their attribution,
fixed source revisions, licenses, and our modification notices are recorded in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
