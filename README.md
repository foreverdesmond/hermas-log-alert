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

它不绑定你的编程语言、框架或日志系统：只要日志是文本文件，就能接；两端放在哪、怎么暴露，也随你的部署习惯。

### 用了能得到什么

- **安静**：WARNING 与 INFO/DEBUG 不再半夜吵你，同一个故障的重复日志会被合并成一条。
- **不漏**：首次出现的故障类别**一定会**送到你手上；CRITICAL 永远实时 —— 聚合只压重复，不压新问题。
- **真的智能**：级别由大模型读日志内容判断，而不是只匹配关键字；每条告警都写清 项目 / 程序 / 日志文件，而不是笼统的"有台机器报错了"。
- **只报你最关心的**：用配置决定哪些项目、哪些程序、哪些级别要实时叫你；其余日志不丢弃，而是被统计与分类，成为后续迭代与故障排查的数据依据。
- **省 Token**：过滤与聚类都在模型之前完成，重复日志不会一遍遍送进大模型，从而有效降低 LLM 的调用量与 Token 消耗。
- **部署灵活**：本机 Docker、云主机、内网机器都能跑；容器用 Portainer、Docker Compose、Swarm、K8s 管都行；接收端可以躲在已有域名的一个路径后面，也可以用公网端口、Nginx 转发 —— 都行。

### 为什么需要它

传统的日志告警，本质上只能做一件事：**用正则表达式匹配日志文本**。命中关键字就给它贴一个"级别"，然后把命中的行原样发出来。

这样做有两个天生的短板：它说不出这条日志**对你的业务意味着什么**；也说不出**除了被命中的这行之外，系统整体正在发生什么**。

把日志系统接到 Hermes 之后，这两件事一起变了：

- **级别不再靠猜**：日志内容直接交给大模型判断，得到的是真正的智能日志 —— 它读得懂上下文，而不是只看有没有关键字。
- **只汇报你最关心的**：通过配置决定哪些项目、哪些程序、哪些级别需要实时叫你，其余的不再打扰你。
- **其余日志不是丢掉，而是变成数据**：系统里其他级别的日志会被统计与分类，成为后续迭代与故障排查的依据 —— 平时没人看的 INFO 与 WARNING，恰恰是趋势和隐患的来源。
- **过滤与聚类在模型之前完成**：大量重复的日志不会一遍遍送进大模型，同类先合并，模型只处理真正有信息量的内容，从而**有效降低 LLM 的调用量与 Token 消耗**。

| | 传统日志告警 | 接入 Hermes 之后 |
|---|---|---|
| 级别判定 | 正则匹配关键字 | 大模型读日志内容判断 |
| 告警内容 | 命中的原始日志行 | 说清影响、建议动作与归属 |
| 汇报范围 | 命中即报，或规则里硬编码 | 按配置只报你关心的 |
| 其余日志 | 丢弃或慢慢堆积 | 统计分类，作为迭代与排查依据 |
| 重复日志 | 每条都发 | 先过滤聚类再进模型，降低 Token 消耗 |

一句话：**从"正则命中了什么"，变成"系统真正发生了什么"。**

### 它解决什么问题

在模型之前先放四道闸，让模型只看真正值得看的东西：

1. **分级分道**：WARNING / INFO / DEBUG / TRACE 只入库，交给每日汇总；ERROR 走实时通道；CRITICAL 永远实时。
2. **正文级别优先**：以日志正文里自带的级别为准（双向）—— 采集端把 INFO 误标成 ERROR 会被归位，正文是 ERROR 而被标低会被升级。含 `failed` / `exception` / `timeout` 等故障词的行**永不降级**。
3. **类别聚合**：同一个主机 + 来源 + 级别 + 分量 + 故障模式的一类问题，默认 1 小时内最多提醒一次（可配置）；同类 5 条/300 秒会立刻升级；被静默的事件仍然全部入库，不会凭空消失。
4. **每日汇总**：每天固定时间把收集到的 WARNING 聚类，交给模型写一份汇总（哪些要人工处理、哪些疑似 bug、哪些是可忽略噪声），并附上用量与看门狗状态。**输出语言与措辞由提示词决定**，随时可以配置和升级。

状态库读不到时会**静默丢弃并留下痕迹**（而不是崩溃或制造模型风暴），异常由下一次汇总的看门狗段落报出来。

### 它不是什么

- **不是日志存储或搜索系统**：不做索引、不做检索，长期留存请继续用你现有的日志方案。
- **不是 SIEM / 合规审计产品**：审计库只是为了告警链自身的可追溯，不是合规证据库。
- **不替代你现有的日志系统**：它只是个"哨兵"，只读你的日志文件，不接管写入。
- **不是一套固定的部署方案**：它只约定两端之间的契约，不管你把服务放在哪、怎么暴露、用什么容器管理 —— 躲在已有域名的一个路径后面可以，公网端口、Nginx 转发、内网直连也都可以。

### 由什么组成

```
agent/            # Docker 采集栈：读日志、过滤、签名、发送（Compose Stack，任何容器管理方式都能部署）
  logtail/        #   基于 vogo/logtail 的目录监听（含 4 个补丁：从尾部开始、带 source 的载荷……）
  alert-gateway/  #   Go 服务：级别判定、去重冷却、项目白名单、HMAC 签名投递、失败重试队列
  projects.json   #   项目 / 程序白名单：true 发送、false 抑制，未列出的一律抑制
hermes-service/   # 接收端：验签 → pre-LLM 闸门 → AI 分诊 → 通知 / 每日汇总
  scripts/        #   闸门、日报、试行期复盘脚本（Python 3，仅标准库 + SQLite）
  config/         #   脱敏后的路由样例与逐字提示词
protocol/         # 双端共用的签名事件契约（本仓库最高优先级的文档）
deploy/           # 部署说明
```

数据流：

```
采集端：日志文件（只读挂载） → 关键字路由 → 项目/程序白名单 → 级别与去重指纹
        → HMAC 签名 → HTTPS 投递
接收端：webhook 入口 → 验签（失败 401 / 按事件 ID 去重 / ±300 秒时效） → pre-LLM 闸门
        → [静默] 只入库 → 每日汇总
        → AI 分诊 → 实时通知
```

细节分别写在 [`agent/README.md`](agent/README.md)、[`hermes-service/README.md`](hermes-service/README.md)，双端契约见 [`protocol/README.md`](protocol/README.md)。

### 快速开始

**1) 采集端**

```bash
cd agent
cp .env.sample config.env      # 或 config.env.sample，两者内容相同
```

填写四个变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `HERMES_WEBHOOK_URL` | 是 | 接收端的完整 webhook 地址 |
| `HERMES_WEBHOOK_SECRET` | 是 | 双端共享的签名密钥（长随机串，勿复用示例值） |
| `ALERT_HOST` | 是 | 告警里显示的主机名，例如 `prod-log-server-01` |
| `LOGS_DIR` | 是 | 宿主日志目录的绝对路径，只读挂载进采集容器 |

然后在 Docker 主机上构建两个镜像，用**你惯用的容器管理方式**部署这个 Stack —— Portainer、Docker Compose 命令行、Swarm、K8s 都可以，没有任何绑定。

再按 [`agent/projects.json`](agent/projects.json) 的白名单决定哪些目录要发：`true` 发送、`false` 抑制，**未列出的目录默认抑制**。改完立即生效，无需重新部署。

**2) 接收端**

```text
① 建一个独立的接收服务身份（独立进程、独立机器人、独立状态库）
② 把 hermes-service/scripts/*.py 放进它自己的 scripts/ 目录（权限 700）
③ 用 config/webhook-subscription.sample.json 做路由配置：
   填入签名密钥与通知目标，并把 config/prompts/alert-triage.md 的内容逐字贴进 prompt
④ 建两个定时任务：每日汇总（脚本 + daily-digest.md 提示词）、可选的试行期复盘
⑤ 把接收端接到你的网络里 —— 随你的部署习惯：
   躲在已有域名的一个路径后面（POST-only、反代到本机监听）、用公网端口、走 Nginx 转发，
   或者只在你的内网里跑，都可以
⑥ 按 hermes-service/README.md 的验收清单逐项核对
```

**3) 验收**

至少确认：健康检查 200、缺失或错误签名 401、一条真实签名的合成事件能收到通知、WARNING 完全静默、同一个 ERROR 类别的多条日志聚成一类。

### 安全与隐私

- **日志正文一律视为不可信数据**：提示词明确禁止执行其中的命令、访问其中的 URL 或据此改配置。正文里"这是测试、无需处理"之类的自述不会被采信。
- **凭据只存本地**：签名密钥只存在于部署环境的本地文件里，不入库、不入档、不进提示词、不进仓库。
- **暴露面由你决定**：本仓库的参考部署是「复用已有域名 + 一个精确路径 + 接收端只监听回环」，但这只是一种做法，不是硬要求 —— 公网端口、Nginx 转发、内网直连都在可选范围内。唯一不可省的是上面那条：密钥只留在本地。
- **可追溯**：每个事件与其处置结果都记在审计库里；闸门自身的故障会留下面包屑，并由汇总的看门狗段落报出。

### 许可证

本仓库以 **Apache License 2.0** 授权，见 [`LICENSE`](LICENSE)（英文原文为唯一有效版本，其后附非官方中文参考译文）；版权归属见 [`NOTICE`](NOTICE)。
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

It binds you to no particular language, framework, or logging stack — if your logs are text files, it can read them. Where the two ends live and how they are exposed is up to your deployment.

### What you get

- **Quiet**: WARNING and INFO/DEBUG lines stop waking you up; repeats of one fault are collapsed into one.
- **Nothing slips through**: a class of failure seen for the first time **always** reaches you, and CRITICAL is always real time — aggregation compresses repeats, never new problems.
- **Genuinely intelligent**: severity is decided by a model reading the log, not by keyword matching, and every alert names the project, the program, and the log file instead of "something on some host broke".
- **Only what you care about**: configuration decides which projects, programs and severities reach you in real time. The rest is not thrown away — it is counted and classified as material for later iterations and troubleshooting.
- **Fewer tokens**: filtering and clustering happen before the model, so repeated lines are not sent again and again — LLM calls and token spend drop accordingly.
- **Flexible deployment**: a laptop, a cloud host, or an internal machine; managed by Portainer, Docker Compose, Swarm, or Kubernetes; the receiver can hide behind one path on an existing domain, or use a public port, an nginx proxy — whatever fits.

### Why it is needed

A traditional log alert can really do only one thing: **match log text against regular expressions**. A keyword hits, a "severity" is stamped on the line, and the matched line is forwarded as-is.

That leaves two blind spots: it cannot say **what this line means for your business**, and it cannot say **what the rest of the system is doing** beyond the lines that happened to match.

Wiring your logs into Hermes changes both:

- **Severity stops being a guess.** The log content goes to a model, which reads context instead of checking for keywords — genuinely intelligent logging.
- **You hear only what you care about.** Configuration decides which projects, which programs and which severities should reach you in real time; everything else stops interrupting you.
- **The rest is not discarded — it becomes data.** Logs at other severities are counted and classified, giving you material for later iterations and troubleshooting. The INFO and WARNING lines nobody watches are exactly where trends and latent faults show up first.
- **Filtering and clustering happen before the model.** Floods of repeated lines are merged first, so the model only ever sees content that carries information — which **substantially reduces LLM calls and token consumption**.

| | Traditional log alerting | Wired into Hermes |
|---|---|---|
| Severity | keyword matching | a model reading the log |
| Alert body | the raw matched line | impact, suggested action, attribution |
| What reaches you | anything that matches, or hard-coded rules | what your configuration cares about |
| Other logs | dropped, or quietly piling up | counted and classified as data |
| Repeated lines | every one is sent | filtered and clustered before the model |

In one sentence: **from "a regex matched something" to "here is what actually happened".**

### How it solves them

Four gates run before the model ever sees anything:

1. **Severity routing**: WARNING / INFO / DEBUG / TRACE are recorded for the digest only; ERROR goes to the real-time path; CRITICAL is always real time.
2. **The log line's own level wins**: in both directions. A line the collector mislabelled as ERROR while its body says INFO is put back, and a line labelled too low while its body says ERROR is escalated. A line containing a real fault word (`failed`, `exception`, `timeout`, …) is **never** downgraded.
3. **Per-class aggregation**: one class of problem — same host, source, level, component, and failure shape — is paged at most once an hour by default (configurable); five in 300 seconds escalate immediately; every suppressed event is still recorded, so nothing disappears.
4. **Daily digest**: once a day the collected WARNING lines are clustered and summarised by a model — what needs a human, what looks like a bug, what is noise — with usage and watchdog status attached. **The output language and wording live in the prompt**, so they can be configured and upgraded at any time.

If the state store cannot be read, the gate **fails silently and leaves a breadcrumb** rather than crashing or waking the model on every line; the next digest reports it in its watchdog block.

### What it is not

- **Not a log store or search engine**: no indexing, no querying — keep your existing logging for retention.
- **Not a SIEM or compliance tool**: the audit store exists for the alert chain's own traceability, not as a compliance record.
- **Not a replacement for your logging**: it is a sentinel that reads your log files; it never takes over writing them.
- **Not a fixed deployment recipe**: it pins down the contract between the two ends and says nothing about where they live, how they are exposed, or which container tooling you use — behind one path on an existing domain, a public port, an nginx proxy, or a purely internal link all work.

### What it is made of

```
agent/            # Docker collector: read, filter, sign, send (Compose stack; any container manager will do)
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
receiver:  webhook entry → signature check (401 on failure / dedup by event id / ±300 s)
           → pre-LLM gate
           → [silent] recorded for the digest only
           → AI triage → real-time notification
```

Details live in [`agent/README.md`](agent/README.md) and [`hermes-service/README.md`](hermes-service/README.md); the contract is [`protocol/README.md`](protocol/README.md).

### Quick start

**1) Collector**

```bash
cd agent
cp .env.sample config.env      # or config.env.sample — the same template
```

Fill in the four variables:

| Variable | Required | Purpose |
|---|---|---|
| `HERMES_WEBHOOK_URL` | yes | full webhook URL of the receiver |
| `HERMES_WEBHOOK_SECRET` | yes | shared signing secret (long random value; never reuse the sample) |
| `ALERT_HOST` | yes | host name shown in alerts, e.g. `prod-log-server-01` |
| `LOGS_DIR` | yes | absolute host path of the log directory, mounted read-only into the collector |

Then build the two images on the Docker host and deploy the stack with **whatever container manager you already use** — Portainer, the Docker Compose CLI, Swarm, or Kubernetes; nothing is tied to one of them.

Use the allow-list in [`agent/projects.json`](agent/projects.json) to decide which directories are sent: `true` sends, `false` suppresses, and **anything unlisted is suppressed**. Changes take effect on the next matching line — no redeploy needed.

**2) Receiver**

```text
1. Create a dedicated identity for the receiver (own process, own bot, own state)
2. Put hermes-service/scripts/*.py in its own scripts/ directory (mode 700)
3. Build the route from config/webhook-subscription.sample.json: fill in the
   signing secret and the notification target, and paste config/prompts/alert-triage.md verbatim
4. Create two scheduled jobs: the daily digest (script + daily-digest.md prompt), and
   optionally the one-off review
5. Attach the receiver to your network however you prefer: behind one path on an
   existing domain (POST-only, reverse-proxied to a local listener), on a public
   port, through an nginx proxy — or purely internally
6. Work through the verification checklist in hermes-service/README.md
```

**3) Verify**

At minimum: the health endpoint returns 200, a missing or wrong signature returns 401, one properly signed synthetic event produces a notification, a WARNING is fully silent, and several lines of the same ERROR class collapse into one class.

### Security and privacy

- **Log bodies are untrusted data.** The prompts forbid following commands found in them, visiting their URLs, or changing configuration because of them. A log line claiming "this is a test, ignore it" is not believed.
- **Credentials stay local.** The signing secret lives only in the deployment's local files — never in the state store, the repository, the prompts, or the logs.
- **Exposure is your call.** The reference deployment reuses an existing domain, allows one exact path, and binds the receiver to loopback — but that is one option, not a requirement: a public port, an nginx proxy, or an internal-only link are all fine. The one part that is not optional is the line above: keep the secret local.
- **Traceable.** Every event and how it was handled is recorded; the gate's own failures leave a breadcrumb that the digest's watchdog block reports.

### License

This repository is licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE) (the English text is authoritative; an unofficial Chinese reference translation follows it); copyright ownership is recorded in [`NOTICE`](NOTICE).
The collector build obtains and modifies third-party components (Logtail, Fwatch, both Apache-2.0); their attribution and pinned revisions are recorded in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### Versions

Current state (2026-09-15):

- Collector images `0.1.0` (built locally, architecture-native)
- Receiver gate `1.6.0`, daily digest `1.5.0` — full history in [`hermes-service/README.md`](hermes-service/README.md)

---

<div align="center">Made with ❤️ by Richy</div>
