# Lightweight log alert stack

This directory is a portable Portainer Stack definition. It reads the host
directory named by `LOGS_DIR` read-only and does not expose a host port.

`logtail` is intentionally isolated on `log_alert_ingest`. Only
`alert-gateway` connects to `log_alert_egress`, allowing it to deliver
signed HTTPS events to Hermes.

Copy `config.env.sample` to a private, untracked `config.env`, then fill in the
values. (`.env.sample` contains the same template for Compose users.) When
deploying through Portainer's Web editor, add every value from that private
file in the Stack environment-variable section. Portainer does not
automatically upload it. The two `local/*:0.1.0` images are deliberate placeholders:
they will be built from pinned arm64 sources in the next step, rather than
pulling an unverified image.

Use `method: timer` initially because this is a macOS shared bind mount through
Docker Desktop. After the shadow test proves file creation and rotation are
observed reliably, `method: os` can be evaluated for lower wake-up overhead.

## Project filtering

Edit the visible `projects.json` file to control which log directories are
sent to Hermes. Its structure is deliberately small:

```json
{
  "projects": {
    "Project name": {
      "programs": {
        "Log-directory-name": true
      }
    }
  }
}
```

`true` sends that program's matching alerts; `false` suppresses them. Any log
directory not listed is also suppressed by default. Changes take effect on the
next matching log line; no Stack redeploy is required. Hermes receives the
host, severity and raw log message, plus a `source` value containing the
project, program, log-file path and detected DLL (when the log names one).
