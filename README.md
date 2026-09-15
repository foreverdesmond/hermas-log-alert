# Hermas Log Alert

Lightweight, AI-assisted log alert pipeline. The repository keeps the Docker
collector and the Hermes receiver together so their signed webhook contract
evolves in one place. It supports Linux Docker hosts, macOS Docker Desktop,
and Windows Docker Desktop running Linux containers; the images are built
natively for the architecture of the build host.

## Layout

- `agent/` — Docker log collector, local filtering, deduplication, and Hermes forwarding.
- `hermes-service/` — Hermes-side webhook receiver, AI triage, notification, and reporting (to be added).
- `protocol/` — shared signed-event contract (to be added).
- `deploy/` — deployment notes and environment-specific assets.

## Secrets and runtime data

Copy `agent/.env.sample` to the untracked visible file `agent/config.env`, fill
in the values, and enter those variables in Portainer when deploying. Do not
commit that file. The `.gitignore` also excludes database files and log files.

`agent/projects.json` is safe to commit when it contains only project and
program names. Use `agent/projects.sample.json` as the generic starting point.
