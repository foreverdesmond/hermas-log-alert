#!/usr/bin/env python3
"""试行期复盘数据采集（给 Hermes cron 的一次性任务用）。

只读；不做任何投递决定。stdout 会被注入 agent 提示词，agent 据此产出一份中文复盘。
观察期起点 = 分级+类别聚合上线的时刻（常量，见 TRIAL_START）。
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Pacific/Auckland")
except Exception:
    LOCAL_TZ = None

HERE = Path(__file__).resolve().parent
STORMS = HERE / "log_storm_state.sqlite3"
HERMES_STATE = HERE.parent / "state.db"
GUARD_ERROR_LOG = HERE / "log_storm_guard_errors.log"
REVIEW_VERSION = "1.0.0"

# 分级分流 + 正文级别优先上线（v1.4.0）→ 类别聚合上线（v1.5.0，本观察期起点）
TRIAL_START = datetime(2026, 9, 14, 11, 35, tzinfo=timezone.utc).timestamp()
# 可对比基线（2026-09-14 上午实测：所有日志逐条送 AI 时期）
BASELINE_CALLS_PER_DAY = 1700
BASELINE_COST_PER_DAY = 1.70
# 分级后的目标（实测 A/B 推算）
TARGET_CALLS_PER_DAY = 365
TARGET_COST_PER_DAY = 0.15

EFF = ("CASE WHEN coalesce(effective_severity,'') IN ('','unknown') "
       "THEN lower(severity) ELSE lower(effective_severity) END")


def local(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%m-%d %H:%M NZ") if LOCAL_TZ else dt.strftime("%m-%d %H:%M UTC")


def main() -> int:
    now = time.time()
    since = TRIAL_START
    hours = max((now - since) / 3600.0, 0.1)
    print("LOGALERT_TRIAL_REVIEW " + REVIEW_VERSION)
    print("观察窗口: %s → %s（%.1f 小时）" % (local(since), local(now), hours))

    try:
        c = sqlite3.connect("file:%s?mode=ro" % STORMS, uri=True, timeout=5).cursor()
        sev = c.execute("select %s, count(*) from received_events where received_at >= ? group by 1 order by 2 desc"
                        % EFF, (since,)).fetchall()
        calls = c.execute("select coalesce(sum(llm_dispatched),0), count(*) from received_events where received_at >= ?",
                          (since,)).fetchone()
        cls = c.execute("""select
              sum(case when suppression_reason like 'class_new:%' then 1 else 0 end),
              sum(case when suppression_reason like 'class_cooldown_expired:%' then 1 else 0 end),
              sum(case when suppression_reason like 'class_storm_summary:%' then 1 else 0 end),
              sum(case when suppression_reason like 'class_cooldown:%' then 1 else 0 end)
            from received_events where received_at >= ?""", (since,)).fetchone()
        mis = c.execute("""select
              sum(case when suppression_reason like 'body_tag_info_collector_%' then 1 else 0 end),
              sum(case when suppression_reason like 'body_tag_warning_collector_%' then 1 else 0 end),
              sum(case when suppression_reason = 'collector_error_label_with_zero_counter' then 1 else 0 end),
              sum(case when lower(severity) in ('warning','info','debug','trace','notice')
                        and lower(coalesce(effective_severity,'')) in ('error','critical') then 1 else 0 end)
            from received_events where received_at >= ?""", (since,)).fetchone()
        top_cooled = c.execute("""select suppression_reason, count(*) n from received_events
                                  where received_at >= ? and suppression_reason like 'class_cooldown:%'
                                  group by 1 order by 2 desc limit 5""", (since,)).fetchall()
        per_hour = c.execute("""select strftime('%m-%d %H:00', received_at, 'unixepoch') h,
                                       count(*), coalesce(sum(llm_dispatched),0)
                                from received_events where received_at >= ? group by 1 order by 1""", (since,)).fetchall()
        print("事件分级: " + ", ".join("%s=%d" % (s, n) for s, n in sev))
        print("实时送 LLM: %d 次 / 共 %d 条事件 → 约 %.0f 次/天（基线 %d 次/天；目标 %d 次/天）"
              % (calls[0], calls[1], calls[0] * 24 / hours, BASELINE_CALLS_PER_DAY, TARGET_CALLS_PER_DAY))
        if cls:
            print("类别聚合: 新类放行=%d 冷却到期=%d 突发升级=%d | 因同类冷却静默=%d 条"
                  % (cls[0] or 0, cls[1] or 0, cls[2] or 0, cls[3] or 0))
        if mis:
            print("采集端级别误标: INFO→error=%d WARN→error=%d 零计数→error=%d | 反向修正(升级)=%d"
                  % (mis[0] or 0, mis[1] or 0, mis[2] or 0, mis[3] or 0))
        print()
        print("【人工确认用】因同类冷却被静默最多的类别（看是否混有真新故障）:")
        for reason, n in top_cooled:
            sample = c.execute("select substr(message,1,120) from received_events where suppression_reason=? limit 1",
                               (reason,)).fetchone()
            print("   %d 条 | %s | 例: %s" % (n, reason, (sample[0] if sample else "").replace("\n", " ")))
        print()
        print("每小时事件/实时数（前 12 小时）:")
        for h, n, k in per_hour[:12]:
            print("   %s  事件 %3d  实时 %3d" % (h, n, k))
    except BaseException as exc:
        print("⚠️WATCHDOG 状态库读取失败: %s: %s" % (type(exc).__name__, str(exc)[:200]))

    try:
        u = sqlite3.connect("file:%s?mode=ro" % HERMES_STATE, uri=True, timeout=5).execute(
            """select count(*), coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0),
                      coalesce(sum(cache_read_tokens),0)
               from sessions where source like '%webhook%' and cast(started_at as real) >= ?""",
            (float(since),)).fetchone()
        n, uin, uout, ucache = (int(x or 0) for x in u)
        cost = uin / 1e6 * 0.15 + uout / 1e6 * 0.60 + ucache / 1e6 * 0.003
        print()
        print("LLM 用量（半价口径）: 调用 %d 次 | 输入 %s | 缓存读 %s | 输出 %s | 约 $%.3f"
              % (n, f"{uin:,}", f"{ucache:,}", f"{uout:,}", cost))
        print("按此速率: 约 %d 次/天、$%.2f/天（基线 $%.2f/天；目标 $%.2f/天）"
              % (n * 24 / hours, cost * 24 / hours, BASELINE_COST_PER_DAY, TARGET_COST_PER_DAY))
    except BaseException as exc:
        print("⚠️WATCHDOG 用量读取失败: %s: %s" % (type(exc).__name__, str(exc)[:160]))

    if GUARD_ERROR_LOG.exists():
        try:
            lines = [l for l in GUARD_ERROR_LOG.read_text(errors="replace").splitlines()
                     if l.split("\t")[0].replace(".", "").isdigit() and float(l.split("\t")[0]) >= since]
            print()
            print("闸门故障面包屑: %d 条%s" % (len(lines), "" if not lines else " → " + lines[-1][:150]))
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
