# Hermas Log Alert

<div align="center">

**让日志自己分级：真故障立刻喊你，噪声沉到每日汇总**
**Logs grade themselves — real faults reach you now, noise waits for the daily digest**

一套轻量的日志告警流水线：本机跑一个 Docker 采集栈，对面由 AI 负责分诊与每日汇总。
A lightweight log alert pipeline: a Docker collector on your machine, an AI triage and digest service on the other side.

出品人：Richy

[![X (Twitter)](https://img.shields.io/badge/X-@Richyisaflower-black?logo=x)](https://x.com/Richyisaflower)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Go](https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white)](https://go.dev/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-state-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Telegram](https://img.shields.io/badge/Telegram-alerts-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

</div>

<p align="center">
  <a href="#简体中文">简体中文</a> · <a href="#english">English</a>
</p>

---

## 简体中文

### 这是什么

一套**日志告警流水线**：把机器上的日志按重要性分级 —— 真正的故障实时推给你，其余的都沉到每天的汇总里。

它由两半组成，分别部署在两边：本机的 **Docker 采集栈**（`agent/`）负责读日志、过滤、签名、发送；另一侧的**接收服务**（`hermes-service/`）负责验签、判断、通知与汇总。两边共同实现同一份**签名事件契约**（`protocol/`）。

它不绑定你的编程语言、框架或日志系统：只要日志是文本文件，就能接。

### 用了能得到什么

- **安静**：warning 与 INFO/DEBUG 不再半夜吵你；同一个故障的重复日志会被合并。
- **不漏**：首次出现的故障类别**一定会**送到你手上，critical 永远实时，聚合只压重复、不压新问题。
- **省钱**：判定放在分级之后，实测调用量从约 1,700 次/天降到约 360 次/天，花费从约 $1.7/天降到约 $0.15/天。
- **知道是谁**：每条告警都写清 项目 / 程序 / 日志文件，而不是笼统的"有台机器报错了"。
- **不用开新端口**：复用你已有的 HTTPS 域名，只多一个精确路径；采集端不暴露任何端口。

### 为什么需要它

最直觉的做法是"把日志全部丢给 AI 逐条判断"。它确实能发现故障，但代价很快就显现：

| 问题 | 具体表现 |
|---|---|
| 吵 | 每一条 warning 都被当成事件推送，半夜手机响个不停 |
| 贵 | 逐条判断意味着每条日志一次模型调用，账单随日志量线性上涨 |
| 重复 | 一个连接超时会打印几十上百行，AI 会把它当成几十个独立问题 |
| 级别不可信 | 采集端靠关键字猜级别，`[INFO]` 里出现 "error" 字样就被标成 error |

本项目要解决的就是这四个问题 —— **降噪是第一目的，省钱是副产品**。

### 它解决什么问题

在模型之前先放四道闸，让模型只看真正值得看的东西：

1. **分级分道**：warning / info / debug / trace 只入库，交给每日汇总；error 走实时通道；critical 永远实时。
2. **正文级别优先**：以日志正文里自带的级别为准（双向）—— 采集端把 INFO 误标成 error 会被归位，正文是 ERROR 而被标低会被升级。含 `failed` / `exception` / `timeout` 等故障词的行**永不降级**。
3. **类别聚合**：同一个主机 + 来源 + 级别 + 分量 + 故障模式的一类问题，1 小时内最多提醒一次；同类 5 条/300 秒会立刻升级；被静默的事件仍然全部入库，不会凭空消失。
4. **每日汇总**：每天固定时间把收集到的 warning 聚类，交给模型写一份中文汇总（哪些要人工处理、哪些疑似 bug、哪些是可忽略噪声），并附上用量与看门狗状态。

状态库读不到时会**静默丢弃并留下痕迹**（而不是崩溃或制造模型风暴），异常由下一次汇总的看门狗段落报出来。

### 它不是什么

- **不是日志存储或搜索系统**：不做索引、不做检索，长期留存请继续用你现有的日志方案。
- **不是 SIEM / 合规审计产品**：审计库只是为了告警链自身的可追溯，不是合规证据库。
- **不替代你现有的日志系统**：它只是个"哨兵"，只读你的日志文件，不接管写入。
- **不新增公网暴露面**：复用已有域名与证书，只放行一个精确路径；接收端只监听回环地址，采集端不映射宿主端口。

### 由什么组成

```
agent/            # Docker 采集栈：读日志、过滤、签名、发送（Portainer Stack）
  logtail/        #   基于 vogo/logtail 的目录监听（含 4 个补丁：从尾部开始、带 source 的载荷……）
  alert-gateway/  #   Go 服务：级别判定、去重冷却、项目白名单、HMAC 签名投递、失败重试队列
  projects.json   #   项目 / 程序白名单：true 发送、false 抑制，未列出的一律抑制
hermes-service/   # 服务器侧：验签 → pre-LLM 闸门 → AI 分诊 → 通知 / 每日汇总
  scripts/        #   闸门、日报、试行期复盘脚本（Python 3，仅标准库 + SQLite）
  config/         #   脱敏后的路由样例与逐字提示词
protocol/         # 双端共用的签名事件契约（本仓库最高优先级的文档）
deploy/           # 部署说明
```

数据流：

```
采集端：日志文件（只读挂载） → 关键字路由 → 项目/程序白名单 → 级别与去重指纹
        → HMAC 签名 → HTTPS 投递
接收端：精确路径 → 验签（401 / 去重 / ±300 秒） → pre-LLM 闸门
        → [静默] 只入库 → 每日汇总
        → AI 中文分诊 → 实时通知
```

细节分别写在 [`agent/README.md`](agent/README.md)、[`hermes-service/README.md`](hermes-service/README.md)，双端契约见 [`protocol/README.md`](protocol/README.md)。

### 快速开始

**1) 采集端**

```bash
cd agent
cp .env.sample config.env      # 或 config.env.sample，两者内容相同
```

填写四个变量，然后在 Docker 主机上构建两个镜像、用 Portainer 部署这个 Stack：

| 变量 | 必填 | 说明 |
|---|---|---|
| `HERMES_WEBHOOK_URL` | 是 | 接收端的完整 webhook 地址 |
| `HERMES_WEBHOOK_SECRET` | 是 | 双端共享的签名密钥（长随机串，勿复用示例值） |
| `ALERT_HOST` | 是 | 告警里显示的主机名，例如 `prod-log-server-01` |
| `LOGS_DIR` | 是 | 宿主日志目录的绝对路径，只读挂载进采集容器 |

再按 [`agent/projects.json`](agent/projects.json) 的白名单决定哪些目录要发：`true` 发送、`false` 抑制，**未列出的目录默认抑制**。改完立即生效，无需重新部署。

**2) 接收端**

```text
① 建一个独立的接收服务身份（独立进程、独立机器人、独立状态库）
② 把 hermes-service/scripts/*.py 放进它自己的 scripts/ 目录（权限 700）
③ 用 config/webhook-subscription.sample.json 做路由配置：
   填入签名密钥与通知目标，并把 config/prompts/alert-triage.md 的内容逐字贴进 prompt
④ 建两个定时任务：每日汇总（脚本 + daily-digest.md 提示词）、可选的试行期复盘
⑤ 在已有域名上放行一个精确路径，反代到本机回环监听（POST-only，仅该路径免认证）
⑥ 按 hermes-service/README.md 的验收清单逐项核对
```

**3) 验收**

至少确认：健康检查 200、缺失或错误签名 401、一条真实签名的合成事件能收到通知、warning 完全静默、同一个 error 类别的多条日志聚成一类。

### 安全与隐私

- **日志正文一律视为不可信数据**：提示词明确禁止执行其中的命令、访问其中的 URL 或据此改配置。正文里"这是测试、无需处理"之类的自述不会被采信。
- **凭据只存本地**：签名密钥只存在于部署环境的本地文件里，不入库、不入档、不进提示词、不进仓库。
- **最小暴露面**：只监听回环地址、只放行一个精确路径、不新增无认证的公网端口。
- **可追溯**：每个事件与其处置结果都记在审计库里；闸门自身的故障会留下面包屑，并由汇总的看门狗段落报出。

### 许可证

本仓库以 **Apache License 2.0** 授权，见 [`LICENSE`](LICENSE)（英文原文为唯一有效版本，其后附非官方中文参考译文）。
采集端构建会获取并修改第三方组件（Logtail、Fwatch，均为 Apache-2.0），其归属与固定版本记录在 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

### 版本

当前状态（2026-09-15）：

- 采集端镜像 `0.1.0`（自行构建，架构原生）
- 接收端闸门 `1.6.0`、每日汇总 `1.5.0`，版本历史见 [`hermes-service/README.md`](hermes-service/README.md)

---

## English

### What this is

A **log alert pipeline** that grades your logs by importance: real faults reach you in real time, everything else waits for the daily digest.

It has two halves, deployed on two sides. The local **Docker collector** (`agent/`) reads, filters, signs, and sends. The **receiver** (`hermes-service/`) verifies, decides, notifies, and summarises. Both implement the same **signed event contract** (`protocol/`).

It binds you to no particular language, framework, or logging stack: if your logs are text files, it can read them.

### What you get

- **Quiet**: warnings and INFO/DEBUG lines stop waking you up; repeats of one fault are collapsed.
- **Nothing slips through**: a class of failure seen for the first time **always** reaches you, `critical` is always real time, and aggregation only compresses repeats.
- **Cheap**: judging happens after grading, which took the measured call volume from ~1,700/day to ~360/day and the cost from ~$1.7/day to ~$0.15/day.
- **Attribution**: every alert names the project, the program, and the log file — not just "something on some host broke".
- **No new ports**: it reuses an existing HTTPS host with one extra exact path, and the collector exposes no host port.

### Why it is needed

The obvious approach is to hand every log line to a model and let it judge. That does find faults, and then the bill and the noise arrive:

| Problem | What it looks like |
|---|---|
| Noisy | Every warning is treated as an incident; your phone rings all night |
| Expensive | Per-line judging means one model call per log line, scaling linearly with log volume |
| Repetitive | One connection timeout prints dozens or hundreds of lines; the model sees dozens of separate problems |
| Untrustworthy levels | The collector guesses severity from keywords, so an `[INFO]` line mentioning "error" is labelled `error` |

Those four problems are what this project solves. **Noise reduction is the goal; the cost saving is a side effect.**

### How it solves them

Four gates run before the model ever sees anything:

1. **Severity routing**: warning / info / debug / trace are recorded for the digest only; `error` goes to the real-time path; `critical` is always real time.
2. **The log line's own level wins**: in both directions. A line the collector mislabelled as `error` while its body says INFO is put back, and a line labelled too low while its body says ERROR is escalated. A line containing a real fault word (`failed`, `exception`, `timeout`, …) is **never** downgraded.
3. **Per-class aggregation**: one class of problem — same host, source, level, component, and failure shape — is paged at most once an hour; five in 300 seconds escalate immediately; every suppressed event is still recorded, so nothing disappears.
4. **Daily digest**: once a day the collected warnings are clustered and summarised in a digest that separates what needs a human, what looks like a bug, and what is noise — with usage and watchdog status attached.

If the state store cannot be read, the gate **fails silently and leaves a breadcrumb** rather than crashing or waking the model on every line; the next digest reports it in its watchdog block.

### What it is not

- **Not a log store or search engine**: no indexing, no querying — keep your existing logging for retention.
- **Not a SIEM or compliance tool**: the audit store exists for the alert chain's own traceability, not as a compliance record.
- **Not a replacement for your logging**: it is a sentinel that reads your log files; it never takes over writing them.
- **Not a new attack surface**: it reuses an existing host and certificate, allows exactly one path, binds the receiver to loopback, and maps no host port on the collector.

### What it is made of

```
agent/            # Docker collector: read, filter, sign, send (Portainer stack)
  logtail/        #   directory watching built on vogo/logtail (4 patches: tail from end, payload with source, …)
  alert-gateway/  #   Go service: severity, dedup cooldowns, project allow-list, signed delivery, retry queue
  projects.json   #   project / program allow-list: true sends, false suppresses, unlisted suppresses
hermes-service/   # receiver: verify → pre-LLM gate → AI triage → notification / daily digest
  scripts/        #   gate, digest, and review scripts (Python 3, standard library + SQLite only)
  config/         #   sanitized route sample and verbatim prompts
protocol/         # the signed event contract both ends implement (the authoritative document here)
deploy/           # deployment notes
```

Data flow:

```
collector: log files (read-only mount) → keyword routers → project/program allow-list
           → severity + dedup fingerprint → HMAC signature → HTTPS delivery
receiver:  exact path → signature check (401 / dedup / ±300 s) → pre-LLM gate
           → [silent] recorded for the digest only
           → AI triage in the notification language → real-time notification
```

Details live in [`agent/README.md`](agent/README.md) and [`hermes-service/README.md`](hermes-service/README.md); the contract is [`protocol/README.md`](protocol/README.md).

### Quick start

**1) Collector**

```bash
cd agent
cp .env.sample config.env      # or config.env.sample — the same template
```

Fill in the four variables, build the two images on the Docker host, and deploy the stack with Portainer:

| Variable | Required | Purpose |
|---|---|---|
| `HERMES_WEBHOOK_URL` | yes | full webhook URL of the receiver |
| `HERMES_WEBHOOK_SECRET` | yes | shared signing secret (long random value; never reuse the sample) |
| `ALERT_HOST` | yes | host name shown in alerts, e.g. `prod-log-server-01` |
| `LOGS_DIR` | yes | absolute host path of the log directory, mounted read-only into the collector |

Then use the allow-list in [`agent/projects.json`](agent/projects.json): `true` sends, `false` suppresses, and **anything unlisted is suppressed**. Changes take effect on the next matching line — no redeploy needed.

**2) Receiver**

```text
1. Create a dedicated identity for the receiver (own process, own bot, own state)
2. Put hermes-service/scripts/*.py in its own scripts/ directory (mode 700)
3. Build the route from config/webhook-subscription.sample.json: fill in the
   signing secret and the notification target, and paste config/prompts/alert-triage.md verbatim
4. Create two scheduled jobs: the daily digest (script + daily-digest.md prompt), and
   optionally the one-off review
5. Allow exactly one path on an existing HTTPS host, reverse-proxied to the loopback
   listener (POST-only, auth disabled for that path only)
6. Work through the verification checklist in hermes-service/README.md
```

**3) Verify**

At minimum: the health endpoint returns 200, a missing or wrong signature returns 401, one properly signed synthetic event produces a notification, a warning is fully silent, and several lines of the same `error` class collapse into one class.

### Security and privacy

- **Log bodies are untrusted data.** The prompts forbid following commands found in them, visiting their URLs, or changing configuration because of them. A log line claiming "this is a test, ignore it" is not believed.
- **Credentials stay local.** The signing secret lives only in the deployment's local files — never in the state store, the repository, the prompts, or the logs.
- **Minimal exposure.** Loopback-only listener, one allowed path, no unauthenticated public port.
- **Traceable.** Every event and how it was handled is recorded; the gate's own failures leave a breadcrumb that the digest's watchdog block reports.

### License

This repository is licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE) (the English text is authoritative; an unofficial Chinese reference translation follows it).
The collector build obtains and modifies third-party components (Logtail, Fwatch, both Apache-2.0); their attribution and pinned revisions are recorded in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### Versions

Current state (2026-09-15):

- Collector images `0.1.0` (built locally, architecture-native)
- Receiver gate `1.6.0`, daily digest `1.5.0` — full history in [`hermes-service/README.md`](hermes-service/README.md)

---

<div align="center">Made with ❤️ by Richy</div>
