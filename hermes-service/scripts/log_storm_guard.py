#!/usr/bin/env python3
"""Pre-LLM deduplication and storm aggregation for Hermes log webhooks.

Reads one JSON alert from stdin.  It emits either ``[SILENT]`` (no Hermes
agent turn) or a compact, enriched JSON alert for the webhook route.

v1.6.0 (Hermes/Tiffany, 2026-09-15)
  * The collector now packs the emitting context into ``source`` instead of a
    bare collector name (live since 2026-09-15 07:00 UTC):
    ``project=<p>; program=<pr>; log_file=/logs/<pr>/<file>.log; dll=<d|unknown>``.
    The log file name is usually date-stamped (``polyjob_20260915.log``), so
    raw ``source`` is NOT stable across midnight: it was part of the class key
    and of the fine fingerprint, which silently reset the per-class cooldown
    every day (one extra page per class per day) and split one daily-rotated
    file into two digest clusters. ``stable_source()`` now collapses only the
    date/sequence part of the ``log_file`` segment; project/program names stay
    verbatim because they legitimately contain digits (Crypto15mSync vs
    Crypto1hSync vs Crypto5mSync must not merge).  Stored/audited ``source``
    is unchanged (display keeps the real path).

v1.5.0 (Hermes/Tiffany, 2026-09-14)
  * Class-level aggregation ("同一分量的同类故障"): the fine-grained fingerprint
    still drives audit + storm counting, but the *real-time decision* now also
    passes through a per-CLASS cooldown, where a class = host + source +
    effective severity + emitting component + message skeleton.
    - a class seen for the FIRST time always goes real time (a new fault can
      never be swallowed);
    - the same class is re-paged at most once per CLASS_COOLDOWN_SECONDS, or
      immediately when it bursts (CLASS_STORM_N within CLASS_STORM_WINDOW);
    - `critical` is exempt: it is always real time, spec unchanged.
    Every suppressed event is still recorded (storm_status plus
    ``suppression_reason='class_cooldown:...'``), so nothing is lost from the
    audit or the daily digest.

v1.4.0 (Hermes/Tiffany, 2026-09-14)
  * The level printed INSIDE the log line (``[INFO]`` / ``[WARN]`` / ``[ERROR]`` /
    ``2026-09-14T19:08:30+12:00 ERROR``) is treated as authoritative over the
    collector's ``severity`` label, in BOTH directions:
      - body says INFO/DEBUG/TRACE, collector says error/warning  -> collected,
        no LLM (measured: 106 such events in one 4.3h window)
      - body says WARN, collector says error                     -> collected
      - body says ERROR/CRITICAL, collector says warning/info    -> escalated to
        the real-time path (today: 0 occurrences, but under-labelling must not
        silence a real error)
    Safety boundary kept: if the text contains a real fault word
    (failed/exception/timeout/...), the line is NEVER downgraded - 40 such lines
    in the same window stayed real time.
  * ``info`` / ``debug`` / ``trace`` / ``notice`` join ``warning`` in the
    collect-only set, so a correctly-labelled INFO never wakes the LLM either.

v1.3.0 (merged 2026-09-14 by Hermes/Tiffany)
  Merge of two independent edits that had overwritten each other:
  * Codex's ``info_counter_suppressed`` rule (kept): a log line the collector
    mislabels as ``error`` while it is really INFO with explicit zero counters
    is recorded for audit but never enters storm accounting or the LLM.
  * Tiffany's fixes (restored):
    - fingerprint collapses log timestamps (``2026-09-14 07:02:11.576``) and
      wall-clock times, so a repeated fault counts as ONE incident instead of
      one incident per log line (measured: one WebSocket-reconnect fault =
      210 lines / 116 fingerprints before the fix);
    - any state-store failure (unopenable, corrupt, transaction error) is
      reported as ``[SILENT]`` with a breadcrumb in
      ``log_storm_guard_errors.log`` instead of escaping as an uncaught
      traceback + exit 1 (which the webhook filter treats as "ignore this
      webhook", leaving the alert channel silently blind);
    - severity routing: ``severity=warning`` is collected for the daily digest
      (recorded with ``llm_dispatched=0``) and never wakes the LLM in real
      time; critical / error / unknown keep the real-time path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path


GUARD_VERSION = "1.6.0"
# Severities collected for the daily digest instead of the real-time LLM path.
# Anything else (critical / error / unknown / a new value the collector starts
# sending) stays real time, so an unexpected severity is never downgraded.
# Collected severities are recorded for the daily digest but never wake the LLM.
COLLECT_ONLY_SEVERITIES = {"warning", "info", "debug", "trace", "notice"}
LOW_BODY_LEVELS = {"info", "debug", "trace", "notice"}
# Level ranks let the body's own level win in both directions (downgrade an
# over-labelled INFO/WARN line, escalate an under-labelled ERROR line).
LEVEL_RANK = {"trace": 0, "debug": 1, "info": 2, "notice": 2, "warning": 3,
              "error": 4, "critical": 5}
COLLECTABLE_BODY_LEVELS = LOW_BODY_LEVELS | {"warning"}
STATE_DB = Path(__file__).with_name("log_storm_state.sqlite3")
ERROR_LOG = Path(__file__).with_name("log_storm_guard_errors.log")
WINDOW_SECONDS = 60
STORM_THRESHOLD = 5
REPEAT_SUMMARY_SECONDS = 300
STORM_SUMMARY_SECONDS = 120
# Class layer (v1.5.0): how often the same CLASS may wake the LLM again, and
# how a burst inside one class forces a page even while cooling down.
CLASS_COOLDOWN_SECONDS = 3600
CLASS_STORM_WINDOW = 300
CLASS_STORM_N = 5
CLASS_STORM_COOLDOWN = 120
CLASS_HEAD_WORDS = 4          # message head kept in the class skeleton
CLASS_LAYER_SEVERITIES = {"error", "unknown"}   # critical is never cooled down
RETENTION_SECONDS = 86_400
EVENT_RETENTION_SECONDS = 30 * 86_400
MAX_MESSAGE_CHARS = 1_200
MAX_STORED_MESSAGE_CHARS = 16_000

UUID_RE = re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.I)
HEX_RE = re.compile(r"\b(?:0x)?[0-9a-f]{12,}\b", re.I)
NUMBER_RE = re.compile(r"\b\d{3,}\b")
# Log timestamps / wall-clock times are not fault identity.
TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d{1,6})?)?\b")
SPACE_RE = re.compile(r"\s+")
INFO_RE = re.compile(r"\[\s*info\s*\]", re.I)
ZERO_COUNTER_RE = re.compile(
    r"\b(?:errors?|errorcount|error_count|failures?|failure_count)\s*[:=]\s*0\b",
    re.I,
)
REAL_FAULT_RE = re.compile(
    r"\b(?:failed?|failure|exception|panic|timeout|timed out|refused|denied|"
    r"unhealthy|crash(?:ed)?|traceback|fatal)\b",
    re.I,
)

# Log lines carry their own level; that is a stronger signal than the
# collector's severity label, so it wins when the two disagree.
LEVEL_ALIAS = {
    "inf": "info", "info": "info", "trc": "trace", "trace": "trace",
    "dbg": "debug", "debug": "debug", "notice": "notice",
    "wrn": "warning", "warn": "warning", "warning": "warning",
    "err": "error", "error": "error", "ftl": "critical", "fatal": "critical",
    "crit": "critical", "critical": "critical",
}
LEVEL_BRACKET_RE = re.compile(
    r"\[(inf|info|trc|trace|dbg|debug|notice|wrn|warn|warning|"
    r"err|error|ftl|fatal|crit|critical)\]",
    re.I,
)
LEVEL_AFTER_TS_RE = re.compile(
    r"(?:\d{2}:\d{2}:\d{2}[.,\d]*|\d{2}:\d{2})\s+"
    r"(inf|info|trc|trace|dbg|debug|notice|wrn|warn|warning|"
    r"err|error|ftl|fatal|crit|critical)\b",
    re.I,
)


def body_level(message: str) -> str | None:
    """Return the level written inside the log line, or None if not stated.

    Only the head of the message is inspected, and only in the two shapes real
    logs use (a bracketed tag, or a level right after a timestamp), so ordinary
    prose mentioning "error" is not mistaken for a level tag.
    """
    head = message[:200]
    m = LEVEL_BRACKET_RE.search(head) or LEVEL_AFTER_TS_RE.search(head)
    return LEVEL_ALIAS.get(m.group(1).lower()) if m else None


def _note_failure(stage: str, exc: BaseException) -> None:
    """Leave a breadcrumb when the guard cannot do its job.

    The guard fails closed (event dropped, no LLM turn), which is the safe
    direction - but a blind alert channel is worse than a loud one, so the
    failure must be discoverable by the daily digest watchdog.
    """
    try:
        line = "%.0f\t%s\t%s: %s\n" % (
            time.time(), stage, type(exc).__name__, str(exc)[:400].replace("\n", " ")
        )
        if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 65_536:
            ERROR_LOG.write_text("", encoding="utf-8")
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
        os.chmod(ERROR_LOG, 0o600)
    except BaseException:
        pass


LOGGER_BRACKET_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9_.]{3,})\]")
BRACKET_SEGMENT_RE = re.compile(r"\[[^\]]*\]")
URL_RE = re.compile(r"https?://\S+")
SKELETON_STRIP_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff<>\[\] .:_-]")
BARE_LEVEL_WORDS = {
    "error", "err", "warn", "warning", "info", "inf", "debug", "dbg",
    "trace", "trc", "critical", "crit", "fatal", "ftl", "notice",
}


# v1.6.0: ``source`` is now a structured collector context
# ("project=…; program=…; log_file=…; dll=…") and the log file name is usually
# date-stamped.  Only the log_file segment is normalised for the grouping keys;
# project/program names are kept verbatim on purpose (they contain meaningful
# digits: Crypto15mSync / Crypto1hSync / Crypto5mSync are different programs).
SOURCE_LOG_FILE_RE = re.compile(r"(log_file=)([^;]*)")
SOURCE_DATE_RE = re.compile(r"\d{4}[-_]?\d{2}[-_]?\d{2}")
SOURCE_LONGNUM_RE = re.compile(r"\b\d{4,}\b")


def stable_source(source: str) -> str:
    """``source`` with the rotation part of ``log_file`` collapsed.

    Display/audit always uses the raw value; this only feeds group keys
    (fingerprint + class), so a daily file rotation cannot reset the cooldown.
    """
    def _norm(match: "re.Match[str]") -> str:
        path = SOURCE_DATE_RE.sub("<date>", match.group(2))
        return match.group(1) + SOURCE_LONGNUM_RE.sub("<n>", path)

    return SOURCE_LOG_FILE_RE.sub(_norm, str(source)[:500])


def class_key(payload: dict, effective_lc: str) -> str:
    """Coarse "same component, same kind of failure" key (v1.5.0).

    Drives only the real-time decision - the fine fingerprint still drives the
    audit and the digest - so it is deliberately looser: emitting component plus
    a message skeleton with all values stripped out.
    """
    raw = str(payload.get("message", ""))[:4096]
    component = ""
    for cand in LOGGER_BRACKET_RE.findall(raw[:400]):
        low = cand.lower()
        if low in BARE_LEVEL_WORDS:
            continue
        if "." in low or len(low) >= 12:
            component = low
            break
    # Drop bracketed segments first (timestamps, levels, component tags), then
    # keep only the head of the message: measured on production traffic, a
    # 4-word head separates genuinely different faults (EventSync tag failure
    # vs. keyset-pagination failure vs. TaskCanceledException each stay their
    # own class) while collapsing value-level differences.
    skel = BRACKET_SEGMENT_RE.sub(" ", raw.lower())
    skel = TIMESTAMP_RE.sub("<ts>", skel)
    skel = CLOCK_RE.sub("<clock>", skel)
    skel = UUID_RE.sub("<uuid>", skel)
    skel = HEX_RE.sub("<hex>", skel)
    skel = URL_RE.sub("<url>", skel)
    skel = NUMBER_RE.sub("<n>", skel)
    skel = SKELETON_STRIP_RE.sub(" ", skel)
    skel = SPACE_RE.sub(" ", skel).strip()
    head = " ".join(skel.split()[:CLASS_HEAD_WORDS])
    if not component:
        component = head[:60]
    material = "\x1f".join([
        str(payload.get("host", ""))[:200],
        stable_source(payload.get("source", "")),
        str(effective_lc)[:32],
        component,
        head[:120],
    ])
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


def normalized_fingerprint(payload: dict, severity_override: str | None = None) -> str:
    """Group the same fault even when IDs, timestamps, or counters differ."""
    message = str(payload.get("message", ""))[:4096].lower()
    message = UUID_RE.sub("<uuid>", message)
    message = HEX_RE.sub("<hex>", message)
    message = TIMESTAMP_RE.sub("<ts>", message)
    message = CLOCK_RE.sub("<clock>", message)
    message = NUMBER_RE.sub("<n>", message)
    message = SPACE_RE.sub(" ", message).strip()
    material = "\x1f".join(
        [
            str(payload.get("host", ""))[:200],
            stable_source(payload.get("source", "")),
            str(severity_override or payload.get("severity", "unknown"))[:32].lower(),
            message,
        ]
    )
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


def is_false_error_info(payload: dict) -> bool:
    """Reject a common collector error: INFO with explicit zero counters."""
    severity = str(payload.get("severity", "unknown")).strip().lower()
    message = str(payload.get("message", ""))[:4096]
    return (
        severity == "error"
        and INFO_RE.search(message) is not None
        and ZERO_COUNTER_RE.search(message) is not None
        and REAL_FAULT_RE.search(message) is None
    )


def _open_state_db() -> sqlite3.Connection:
    STATE_DB.touch(mode=0o600, exist_ok=True)
    os.chmod(STATE_DB, 0o600)
    db = sqlite3.connect(STATE_DB, timeout=2, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=2000")
    return db


def _safe_rollback(db: sqlite3.Connection) -> None:
    try:
        if db.in_transaction:
            db.execute("ROLLBACK")
    except BaseException as exc:
        _note_failure("rollback", exc)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        print("[SILENT]")
        return 0
    if not isinstance(payload, dict):
        print("[SILENT]")
        return 0

    now = time.time()
    raw_message = str(payload.get("message", ""))[:MAX_STORED_MESSAGE_CHARS]
    message = raw_message[:MAX_MESSAGE_CHARS]
    severity = str(payload.get("severity", "unknown"))[:32]
    severity_lc = severity.strip().lower()

    # v1.4.0: decide the EFFECTIVE severity before anything else, because it
    # drives routing, the fingerprint, and the audit columns.
    body = body_level(raw_message)
    downgraded_to: str | None = None
    escalated = False
    if body is not None:
        body_rank = LEVEL_RANK.get(body, 3)
        coll_rank = LEVEL_RANK.get(severity_lc, 3)  # unknown/unlisted acts warning-ish
        fault_word = REAL_FAULT_RE.search(raw_message) is not None
        if body in COLLECTABLE_BODY_LEVELS and coll_rank > body_rank and not fault_word:
            # Collector over-labelled a plain INFO/WARN line: collect it instead.
            # The fault-word veto applies ONLY here - never to escalation.
            downgraded_to = body
        elif body in ("error", "critical") and coll_rank < body_rank:
            # Collector under-labelled a real ERROR: never let that silence it.
            escalated = True
    effective_lc = downgraded_to or (body if escalated else severity_lc) or "unknown"
    collect_only = effective_lc in COLLECT_ONLY_SEVERITIES and not escalated
    key = normalized_fingerprint(payload, severity_override=effective_lc)
    # Codex's zero-counter rule stays active as an extra net for lines whose head
    # carries no usable level (e.g. an ERROR head that still reports Errors=0).
    false_error_info = is_false_error_info(payload)

    try:
        db = _open_state_db()
    except BaseException as exc:
        # Cannot even reach the state store: stay silent (no LLM flood) but
        # leave evidence, because the whole alert path is effectively down.
        _note_failure("open", exc)
        print("[SILENT]")
        return 0

    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS storm_groups (
                   fingerprint TEXT PRIMARY KEY,
                   window_started REAL NOT NULL,
                   last_seen REAL NOT NULL,
                   events_in_window INTEGER NOT NULL,
                   last_agent_sent REAL NOT NULL,
                   last_storm_sent REAL,
                   sample_message TEXT NOT NULL
               )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS received_events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   received_at REAL NOT NULL,
                   incident_id TEXT,
                   host TEXT,
                   source TEXT,
                   severity TEXT,
                   event_timestamp TEXT,
                   message TEXT NOT NULL,
                   fingerprint TEXT NOT NULL,
                   storm_count INTEGER NOT NULL,
                   storm_window_seconds INTEGER NOT NULL,
                   storm_status TEXT NOT NULL,
                   llm_dispatched INTEGER NOT NULL
               )"""
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(received_events)")}
        if "effective_severity" not in columns:
            db.execute("ALTER TABLE received_events ADD COLUMN effective_severity TEXT NOT NULL DEFAULT 'unknown'")
        if "suppression_reason" not in columns:
            db.execute("ALTER TABLE received_events ADD COLUMN suppression_reason TEXT NOT NULL DEFAULT ''")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_received_events_time "
            "ON received_events(received_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_received_events_fingerprint "
            "ON received_events(fingerprint, received_at)"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS class_state (
                   class_key TEXT PRIMARY KEY,
                   window_started REAL NOT NULL,
                   last_seen REAL NOT NULL,
                   events_window INTEGER NOT NULL,
                   events_total INTEGER NOT NULL,
                   last_agent_sent REAL NOT NULL,
                   last_storm_sent REAL,
                   sample_message TEXT NOT NULL
               )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_class_state_seen ON class_state(last_seen)"
        )
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM storm_groups WHERE last_seen < ?", (now - RETENTION_SECONDS,))
        db.execute("DELETE FROM class_state WHERE last_seen < ?", (now - RETENTION_SECONDS,))
        db.execute("DELETE FROM received_events WHERE received_at < ?", (now - EVENT_RETENTION_SECONDS,))
        row = db.execute(
            "SELECT window_started,last_seen,events_in_window,last_agent_sent,"
            "last_storm_sent,sample_message FROM storm_groups WHERE fingerprint=?",
            (key,),
        ).fetchone()

        if downgraded_to is not None:
            # v1.4.0 rule: the log line's own level says INFO/DEBUG/TRACE (or
            # WARN) while the collector claimed error/warning.  Record it for
            # the digest, keep it out of storm accounting, never call the LLM.
            db.execute(
                """INSERT INTO received_events
                   (received_at,incident_id,host,source,severity,event_timestamp,message,
                    fingerprint,storm_count,storm_window_seconds,storm_status,llm_dispatched,
                    effective_severity,suppression_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now, str(payload.get("incident_id", ""))[:300],
                    str(payload.get("host", ""))[:200], str(payload.get("source", ""))[:500],
                    severity, str(payload.get("timestamp", ""))[:100],
                    raw_message, key, 1, WINDOW_SECONDS, "body_level_suppressed", 0,
                    downgraded_to,
                    "body_tag_%s_collector_%s" % (downgraded_to, severity_lc or "unknown"),
                ),
            )
            db.execute("COMMIT")
            print("[SILENT]")
            return 0

        if false_error_info:
            # Codex rule: record every false error for audit, but do not let the
            # collector's bad severity label enter storm accounting or consume
            # LLM tokens.
            db.execute(
                """INSERT INTO received_events
                   (received_at,incident_id,host,source,severity,event_timestamp,message,
                    fingerprint,storm_count,storm_window_seconds,storm_status,llm_dispatched,
                    effective_severity,suppression_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now, str(payload.get("incident_id", ""))[:300],
                    str(payload.get("host", ""))[:200], str(payload.get("source", ""))[:500],
                    severity, str(payload.get("timestamp", ""))[:100],
                    raw_message, key, 1, WINDOW_SECONDS, "info_counter_suppressed", 0,
                    "warning", "collector_error_label_with_zero_counter",
                ),
            )
            db.execute("COMMIT")
            print("[SILENT]")
            return 0

        if row is None:
            db.execute(
                """INSERT INTO storm_groups
                   (fingerprint,window_started,last_seen,events_in_window,last_agent_sent,last_storm_sent,sample_message)
                   VALUES (?, ?, ?, 1, ?, NULL, ?)
                   ON CONFLICT(fingerprint) DO UPDATE SET
                     window_started=excluded.window_started, last_seen=excluded.last_seen,
                     events_in_window=1, last_agent_sent=excluded.last_agent_sent,
                     last_storm_sent=NULL, sample_message=excluded.sample_message""",
                (key, now, now, now, message),
            )
            status, count, should_send = "first_seen", 1, True
        elif now - row[1] > WINDOW_SECONDS:
            # A sparse repeat begins a fresh storm-counting window, but it is
            # still the same fault fingerprint.  Do not re-dispatch every
            # minute merely because the preceding burst stopped briefly.
            _window_started, _last_seen, _old_count, last_sent, _last_storm_sent, _sample = row
            should_send = now - last_sent >= REPEAT_SUMMARY_SECONDS
            status = "repeat_summary" if should_send else "duplicate_suppressed"
            db.execute(
                """UPDATE storm_groups SET window_started=?,last_seen=?,events_in_window=1,
                   last_agent_sent=CASE WHEN ? THEN ? ELSE last_agent_sent END,
                   last_storm_sent=NULL,sample_message=? WHERE fingerprint=?""",
                (now, now, int(should_send), now, message, key),
            )
            count = 1
        else:
            window_started, _last_seen, count, last_sent, last_storm_sent, sample = row
            count += 1
            db.execute(
                "UPDATE storm_groups SET last_seen=?,events_in_window=? WHERE fingerprint=?",
                (now, count, key),
            )
            if count >= STORM_THRESHOLD:
                if last_storm_sent is None or now - last_storm_sent >= STORM_SUMMARY_SECONDS:
                    db.execute(
                        "UPDATE storm_groups SET last_agent_sent=?,last_storm_sent=? WHERE fingerprint=?",
                        (now, now, key),
                    )
                    status, should_send = "storm_summary", True
                else:
                    status, should_send = "storm_suppressed", False
            elif now - last_sent >= REPEAT_SUMMARY_SECONDS:
                db.execute(
                    "UPDATE storm_groups SET last_agent_sent=? WHERE fingerprint=?",
                    (now, key),
                )
                status, should_send = "repeat_summary", True
            else:
                status, should_send = "duplicate_suppressed", False

        # Severity routing: collected severities are still recorded (with
        # llm_dispatched=0) for the daily digest, but never wake the LLM in real
        # time.  Storm counters keep running so the digest can report frequency.
        suppression_reason = ""
        if collect_only:
            should_send = False
            suppression_reason = "severity_collected_for_daily_digest"

        # v1.5.0 class layer: the real-time decision is aggregated per class.
        # A brand-new class always goes through; a known class is re-paged at
        # most once per cooldown, unless it bursts (then it pages immediately).
        class_note = ""
        if should_send and effective_lc in CLASS_LAYER_SEVERITIES:
            cls = class_key(payload, effective_lc)
            crow = db.execute(
                "SELECT window_started,events_window,last_agent_sent,last_storm_sent "
                "FROM class_state WHERE class_key=?", (cls,),
            ).fetchone()
            if crow is None:
                db.execute(
                    "INSERT INTO class_state (class_key,window_started,last_seen,"
                    "events_window,events_total,last_agent_sent,last_storm_sent,sample_message) "
                    "VALUES (?,?,?,1,1,?,NULL,?)",
                    (cls, now, now, now, message),
                )
                class_note = "class_new"
            else:
                c_window, c_events, c_last_sent, c_last_storm = crow
                if now - c_window > CLASS_STORM_WINDOW:
                    c_window, c_events = now, 1
                else:
                    c_events += 1
                burst = (c_events >= CLASS_STORM_N and (
                    c_last_storm is None or now - c_last_storm >= CLASS_STORM_COOLDOWN))
                if burst:
                    db.execute(
                        "UPDATE class_state SET window_started=?,last_seen=?,events_window=?,"
                        "events_total=events_total+1,last_agent_sent=?,last_storm_sent=?,"
                        "sample_message=? WHERE class_key=?",
                        (c_window, now, c_events, now, now, message, cls),
                    )
                    class_note = "class_storm_summary"
                elif now - c_last_sent >= CLASS_COOLDOWN_SECONDS:
                    db.execute(
                        "UPDATE class_state SET window_started=?,last_seen=?,events_window=?,"
                        "events_total=events_total+1,last_agent_sent=?,sample_message=? "
                        "WHERE class_key=?",
                        (c_window, now, c_events, now, message, cls),
                    )
                    class_note = "class_cooldown_expired"
                else:
                    db.execute(
                        "UPDATE class_state SET window_started=?,last_seen=?,events_window=?,"
                        "events_total=events_total+1,sample_message=? WHERE class_key=?",
                        (c_window, now, c_events, message, cls),
                    )
                    should_send = False
                    class_note = "class_cooldown"
                    suppression_reason = "class_cooldown:%s" % cls[:12]
            if class_note and not suppression_reason.startswith("class_cooldown"):
                # Audit which class-layer decision allowed (or blocked) this page.
                suppression_reason = "%s:%s" % (class_note, cls[:12])

        db.execute(
            """INSERT INTO received_events
               (received_at,incident_id,host,source,severity,event_timestamp,message,
               fingerprint,storm_count,storm_window_seconds,storm_status,llm_dispatched,
               effective_severity,suppression_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                str(payload.get("incident_id", ""))[:300],
                str(payload.get("host", ""))[:200],
                str(payload.get("source", ""))[:500],
                severity,
                str(payload.get("timestamp", ""))[:100],
                raw_message,
                key,
                count,
                WINDOW_SECONDS,
                status,
                int(should_send),
                effective_lc or "unknown",
                suppression_reason,
            ),
        )
        db.execute("COMMIT")
    except BaseException as exc:
        _safe_rollback(db)
        # Fail closed during a storm: never turn a state-store problem into an
        # LLM flood.  Breadcrumb so a blind alert channel is detectable.
        _note_failure("state-store", exc)
        print("[SILENT]")
        return 0
    finally:
        try:
            db.close()
        except BaseException as exc:
            _note_failure("close", exc)

    if not should_send:
        print("[SILENT]")
        return 0

    payload["message"] = message
    payload["storm_status"] = status
    payload["storm_count"] = count
    payload["storm_window_seconds"] = WINDOW_SECONDS
    payload["storm_fingerprint"] = key[:12]
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
