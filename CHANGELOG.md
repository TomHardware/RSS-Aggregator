# Changelog

本项目所有值得注意的变更都记录在此文件。
*All notable changes to this project are documented in this file.*

格式参考 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本号遵循语义化版本。
*The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to Semantic Versioning.*

---

## [2.0.4] - 2026-10-11

本地 GUI 版本：新增桌面窗口与网页控制台，并修复 4 项缺陷、补齐任务可见性功能。
*Local GUI release: adds a desktop window and a web console, fixes four bugs and completes the task-visibility features.*

### 新增功能 / Added

- **本地桌面 GUI**：运行 `python desktop_gui.py` 打开原生窗口（tkinter，随 Python 自带，无需安装新依赖）。
- *Local desktop GUI: run `python desktop_gui.py` to open a native window (tkinter ships with Python, no extra dependency).*
- 桌面窗口内可管理订阅源：新增、编辑、暂停/恢复、删除，并显示每个源的未读数与上次抓取时间。
- *Manage feeds in the desktop window: add, edit, pause/resume and delete, with the unread count and last fetch time for each feed.*
- 桌面窗口内可阅读文章列表、双击打开原文，并自动标记为已读。
- *Read articles in the desktop window: browse the list, double-click to open the original page, which marks it as read.*
- 桌面窗口内嵌启动同一套 Web 服务，因此浏览器可以同时访问同一份数据；窗口状态栏实时显示抓取进展。
- *The desktop window starts the same web service in the background, so the browser can be used at the same time; the status bar shows fetch progress live.*
- **网页控制台**：访问 `/console` 得到一个紧凑的仪表盘——订阅源一览、未读数、批量「全部抓取」，以及暂停/恢复、编辑、删除等操作。
- *Web console: open `/console` for a compact dashboard — a feed overview, unread counts, a bulk "Fetch all" action, plus pause/resume, edit and delete.*
- **全部抓取**：新增一键抓取全部启用中的订阅源；后台并发已满时自动改为同步抓取。
- *Fetch all: one click fetches every enabled feed; when the background concurrency limit is reached it falls back to synchronous fetching.*
- **共享业务层**：网页、控制台与桌面 GUI 共用 `app/services.py`，三端行为一致。
- *Shared service layer: the web UI, the console and the desktop GUI all use `app/services.py`, so all three behave identically.*

### 修复 / Fixed

- **抓取条数上限设为 0 时会一条文章都存不进来**：现在 0 表示不限条数，与其他配置一致。
- *Setting the fetch entry cap to 0 stored nothing at all: 0 now means unlimited, consistent with the other settings.*
- **配置明文口令时每次登录都要重新计算一次慢哈希**：现在只派生一次并缓存。
- *A plaintext password was re-hashed on every login: it is now derived once and cached.*
- **登录失败记录只增不减**：现在会清理过期记录并限制总量，长期运行不再持续占用内存。
- *Login failure records only ever grew: expired entries are now pruned and the total is capped.*
- **文章保留策略在保留量很大时可能超出数据库参数上限**：改为子查询，保留量再大也不会出错。
- *Article retention could exceed the database parameter limit: it now uses a subquery, so large limits are safe.*

### 仓库整理 / Repository housekeeping

- 补回 `.dockerignore`，缺少它会把 `.git` 与本地数据库打进镜像。
- *Restored `.dockerignore`; without it, `.git` and the local database were baked into the image.*
- 删除 `app/.dockerfile`（已被根目录 `Dockerfile` 取代）与误提交的 `app/web/__pycache__/`。
- *Removed `app/.dockerfile` (superseded by the root `Dockerfile`) and the accidentally committed `app/web/__pycache__/`.*

## [2.0.2] - 2026-10-07

第五轮审查 + 任务可见性：修复 **4 项缺陷**，新增 **任务状态接口与前端轮询**。
Fifth audit plus task visibility: **4 bugs fixed** and a **task status API with front-end polling**.

### 新增功能 / Added

- **任务状态接口 `GET /api/status`**：返回正在运行 / 最近完成的后台抓取任务、
  未读总数、源数量、服务端时间与版本号；`GET /api/tasks/<id>` 查询单个任务。
  只读、无副作用，未登录时返回 JSON 401（不再把 `fetch()` 重定向到登录页）。
- *`GET /api/status`: returns running and recently finished background fetch tasks, the unread total, the feed count, server time and the version. `GET /api/tasks/<id>` looks up a single task. Read-only, and when logged out it answers with a JSON 401 instead of redirecting a `fetch()` to the HTML login page.*

- **前端轮询**：首页每 5 秒轮询一次接口，显示「正在抓取：某源」/「已抓到 N 篇」，
  并在一次抓取刚结束时自动刷新页面——添加源之后不用再手动刷新就能看到新文章。
- *Front-end polling: the index page polls every five seconds, shows "Fetching: feed" or "Fetched N new articles", and reloads itself when a fetch finishes, so new articles appear without a manual refresh.*

- **并发上限 `tasks.max_concurrent`（默认 5）**：后台任务达到上限时，
  「添加源」退回同步抓取、定时抓取跳过本轮，而不是无限起线程。
- *Concurrency cap `tasks.max_concurrent` (default 5): once the limit is reached, adding a feed falls back to a synchronous fetch and scheduled runs are skipped instead of spawning unbounded threads.*

### 修复 / Fixed

- **明文口令每次登录都重新派生哈希**：配置里写 `auth.password` 时，
  `generate_password_hash`（故意很慢）在每次登录尝试时都会再跑一遍；现在派生一次并缓存。
- *Plaintext passwords were re-hashed on every login: with `auth.password` configured, `generate_password_hash` (deliberately slow) ran again on every attempt; it is now derived once and cached.*

- **失败记录表从不清理**：`LoginThrottle` 以来源 IP 为键，键只增不减，长期运行会持续占内存；
  现在定期清理过期项，并给键数量设上限。
- *The failure table was never pruned: `LoginThrottle` keyed entries by client address and only ever grew. Expired entries are now pruned periodically and the key count is capped.*

- **`/api/` 未登录时返回 HTML 重定向**：轮询脚本会拿到登录页 HTML 而不是可解析的响应；
  现在统一返回 JSON 401，前端据此刷新页面重新登录。
- *`/api/` returned an HTML redirect when logged out: the polling script received the login page instead of a parseable response. It now returns a JSON 401 and the script reloads the page to sign in again.*

- **`retention.py` 里的动态 import**：`__import__("datetime").timedelta(...)` 改为正常的模块级导入。
- *Dynamic import in `retention.py`: `__import__("datetime").timedelta(...)` replaced with a normal module-level import.*

- **未读统计加了 3 秒短缓存**：页面每 5 秒轮询一次，避免把分组统计压到每次轮询上。
- *Unread counts gained a three-second cache so the five-second polling does not run the grouping query on every request.*

- **打包遗漏（第四次）**：补回 `.dockerignore`、`.gitignore` 运行时项，删除 `app/.dockerfile`。
- *Packaging gaps, fourth time: restored `.dockerignore` and the `.gitignore` runtime rules, removed `app/.dockerfile`.*

> **本轮开发中自己引入并被测试抓住的缺陷**：给 `LoginThrottle` 加清理逻辑时，
> `_trim()` 会把空窗口从字典里删掉，于是 `setdefault` 新建的窗口被删后，
> 调用方 append 的是游离的 `deque`——**首次失败根本记不进去，限流直接失效**。
> 已修复，并由 `test_login_throttle_prunes_stale_entries` 与登录限流用例双重覆盖。
>
> **A bug introduced and caught during this round**: while adding pruning to `LoginThrottle`,
> `_trim()` deleted empty windows from the dict, so the window created by `setdefault` was
> removed and the caller appended to a detached `deque` — **the first failure was never
> recorded and throttling silently stopped working**. Fixed, and covered by both
> `test_login_throttle_prunes_stale_entries` and the login-throttling test.

### 测试 / Tests

| 套件 / Suite | 结果 / Result |
|---|---|
| `tests/test_bugfixes.py`（30 例 / cases） | 30 / 30 |
| `tests/test_v13_bugs.py`（8 例 / cases） | 8 / 8 |
| `tests/test_v14_bugs.py`（7 例 / cases） | 7 / 7 |
| `tests/test_v2_bugs.py`（16 例 / cases） | 16 / 16 |
| `tests/test_v202_bugs.py`（本轮新增 14 例 / 14 new cases） | 14 / 14 |
| `tests/test_e2e_smoke.py`（端到端 / end-to-end） | 通过 / passed |
| `tests/test_debug_reloader.py`（进程级 / process-level） | 调度器启动 1 次 / 1 scheduler |

## [2.0.1] - 2026-10-07

第四轮审查 + 功能版本：修复 **5 项缺陷**、新增 **5 项功能**，并把版本号收敛到单一来源。
Fourth audit and feature release: **5 bugs fixed**, **5 features added**, and the version number now has a single source of truth.

> 你要求发到 v2.0.1。主版本升到 2 是因为「登录鉴权 + 首次抓取改为后台」属于行为变更，
> 详见下方 2.0.0 小节。
> You asked for v2.0.1. The major version moved to 2 because authentication and the asynchronous
> first fetch change behaviour — see the 2.0.0 section below.

### 新增功能 / Added

- **登录鉴权（单用户）**：`config.yaml` 配置 `auth.password_hash`（生成方式
  `python -m app.auth <password>`），或用环境变量 `RSS_AGGREGATOR_PASSWORD`；
  未配置口令时鉴权自动关闭并在启动日志里给出醒目警告，不会把现有部署锁在门外。
  同一来源连续失败 5 次会锁定 5 分钟，抵挡在线爆破。
- *Single-user login: set `auth.password_hash` in `config.yaml` (generate it with `python -m app.auth <password>`) or provide `RSS_AGGREGATOR_PASSWORD`. When no password is configured, authentication stays off and a prominent warning is logged, so existing deployments are not locked out. Five consecutive failures from one source trigger a five-minute lockout.*

- **订阅源编辑与暂停**：可修改名称 / 分类 / 更新间隔，也可暂停某个源
  （暂停后不再调度，但保留已抓到的文章）。README 与翻译表里一直写着 `edit`，
  但此前没有对应实现。
- *Edit and pause feeds: change name, category and interval, or pause a feed (pausing stops scheduling but keeps the articles already fetched). The README and the translation table had advertised `edit` for a long time without an implementation.*

- **未读统计**：首页显示全局未读总数与每个源的未读数，未读列表用上了新的复合索引。
- *Unread counters: the index shows a global unread total and a per-feed unread count, backed by a new composite index.*

- **文章保留策略**：`retention.max_articles_per_source` / `retention.max_age_days`
  在每次抓取后自动裁剪，避免自托管实例的数据库无限膨胀。
- *Article retention: `retention.max_articles_per_source` and `retention.max_age_days` trim articles after each fetch so a self-hosted instance does not grow without bound.*

- **首次抓取改为后台执行**：`fetch.initial_async`（默认开启）。原来添加源会在请求里
  同步抓取，最坏要等「网络重试 × 超时 + 全文抽取配额」几十秒。
- *Asynchronous first fetch: `fetch.initial_async` (on by default). Adding a feed used to fetch synchronously inside the request, worst case tens of seconds of retries plus full-text extraction.*

### 修复 / Fixed

- **`fetch.max_entries` 会永久丢文章**：原来直接切 `feed.entries[:N]`，一个源一次发布 200 条时，
  第 51 条之后的内容被静默丢弃、再也不会入库。现在遍历全部条目，上限只约束**本次新增条数**，
  剩下的留给后续抓取补齐。
- *`fetch.max_entries` permanently dropped articles: the code sliced `feed.entries[:N]`, so when a feed published 200 items the ones after the 50th were silently discarded forever. It now walks every entry and the cap only limits how many new articles are stored per run; the rest are picked up on later fetches.*

- **老数据库升级会直接报错**：`db.create_all()` 不会给已有表补列，新增 `source.enabled` 后
  老库一启动就因缺列失败。`app/schema.py` 现在会幂等补列（`ALTER TABLE ... ADD COLUMN`）
  与补索引。
- *Upgrading an existing database failed outright: `db.create_all()` never adds columns to existing tables, so the new `source.enabled` column would break every old database on start. `app/schema.py` now backfills missing columns (`ALTER TABLE ... ADD COLUMN`) and indexes idempotently.*

- **会话 Cookie 与请求体缺少加固**：`SESSION_COOKIE_SAMESITE = None`、`MAX_CONTENT_LENGTH = None`
  在 Flask 默认配置里**已经存在**，用 `setdefault` 根本改不动——现在显式设为
  `Lax` / `HttpOnly`，并给请求体加上限（默认 8 MB），超限返回 413。
- *Session cookies and request bodies were not hardened: `SESSION_COOKIE_SAMESITE = None` and `MAX_CONTENT_LENGTH = None` already exist in Flask's default config, so `setdefault` silently did nothing — they are now assigned explicitly (`Lax`, `HttpOnly`), and request bodies are capped (8 MB by default) with a 413 response.*

- **模板里的带参翻译会 500**：`_('unread_total', count=3)` 这类调用要求翻译函数接受关键字参数，
  原来只接受一个 `key`，一渲染就抛 `TypeError`。
- *Parameterised translations crashed the page: calls like `_('unread_total', count=3)` need the translator to accept keyword arguments; the old lambda accepted only `key` and raised `TypeError` while rendering.*

- **应用工厂在上下文之外读配置**：判断「是否启用鉴权」时用了 `current_app`，
  在 `create_app()` 阶段会抛 `RuntimeError: Working outside of application context`。
- *The app factory read config outside a context: the authentication check used `current_app`, which raises `RuntimeError: Working outside of application context` during `create_app()`.*

- **调度器时区配置从未生效**：`app.timezone` 一直被读进配置却没人使用，现在真正传给
  `BackgroundScheduler`（写错时回退系统时区并告警）。
- *The configured scheduler timezone was never applied: `app.timezone` was loaded but unused; it is now passed to `BackgroundScheduler` (falling back to the system timezone with a warning if invalid).*

- **打包遗漏（第三次）**：补回 `.dockerignore`、`.gitignore` 运行时项，删除 `app/.dockerfile`。
- *Packaging gaps, third time: restored `.dockerignore` and the `.gitignore` runtime rules, removed `app/.dockerfile`.*

### 版本号 / Version

- 新增 `app/version.py` 作为版本号唯一来源，`/healthz` 与页面页脚都会显示它；
  测试会校验它与 `CHANGELOG.md`、两份 README 一致。
- *`app/version.py` is now the single source of truth; `/healthz` and the page footer expose it, and a test asserts it matches `CHANGELOG.md` and both READMEs.*

### 测试 / Tests

| 套件 / Suite | 结果 / Result |
|---|---|
| `tests/test_bugfixes.py`（30 例 / cases） | 30 / 30 |
| `tests/test_v13_bugs.py`（8 例 / cases） | 8 / 8 |
| `tests/test_v14_bugs.py`（7 例 / cases） | 7 / 7 |
| `tests/test_v2_bugs.py`（本轮新增 16 例 / 16 new cases） | 16 / 16 |
| `tests/test_e2e_smoke.py`（端到端 / end-to-end） | 通过 / passed |
| `tests/test_debug_reloader.py`（进程级 / process-level） | 调度器启动 1 次 / 1 scheduler |

---

## [2.0.0] - 2026-10-07

**破坏性变更汇总（自 1.x 起）**。本次没有单独发布 2.0.0，2.0.1 直接包含下列全部变更，
这里列出是为了说明主版本号为何升到 2。
**Summary of breaking changes since 1.x.** No 2.0.0 release was published separately; 2.0.1 includes everything below. This section exists to explain the major version bump.

- **登录鉴权**：一旦配置口令，所有页面与接口都要求登录（`/healthz` 保持公开）。
- *Authentication: once a password is configured, every page and endpoint requires a login (`/healthz` stays public).*
- **首次抓取异步化**：`POST /add_source` 立即返回，抓取在后台完成，页面上的文章会稍后出现。
- *Asynchronous first fetch: `POST /add_source` returns immediately; the fetch happens in the background and articles appear shortly after.*
- **删除源 / 刷新源 / 标记已读未读只接受 POST**（1.3 起），跨站 POST 返回 403。
- *State-changing endpoints only accept POST (since 1.3) and cross-site POSTs return 403.*
- **数据库默认路径**：`sqlite:///rss.db` → `sqlite:///data/rss.db`（容器内即挂载卷 `/app/data`）。
- *Default database path: `sqlite:///rss.db` → `sqlite:///data/rss.db` (i.e. `/app/data` in the container).*
- **默认拒绝内网抓取**：需要内网源时设置 `security.allow_private_networks: true`。
- *Internal addresses are rejected by default: opt in with `security.allow_private_networks: true`.*
- **全文抽取默认每次最多 5 篇**；`fetch.max_entries` 语义改为「每次新增上限」。
- *Full-text extraction defaults to 5 articles per run, and `fetch.max_entries` now means "max new articles per run".*
- **配置项类型写错会直接启动失败并指明位置**（`ConfigError`）。
- *A wrongly typed config value now fails startup with a `ConfigError` naming the offending key.*

---

## [1.5.0] - 2026-10-07

第三轮审查版本：修复 **6 项缺陷**（含一处可稳定触发 500 的输入处理问题），并补回 v1.4 上传时再次丢失的文件。
Third audit release: **6 bugs fixed** (including input handling that reliably caused a 500), plus files that went missing again during the v1.4 upload.

### 安全 / Security

- **`next` 参数里的控制字符会打崩请求**：只要在「标记已读」请求中把 `next` 填成含换行的值，
  Werkzeug 的响应头注入检查就会抛 `ValueError: Header values must not contain newline characters`，
  请求返回 500。现在 `_local_redirect` 直接拒绝含控制字符的目标，回落到首页。
- *Control characters in the `next` parameter crashed the request: posting a `next` value containing a newline made Werkzeug's header-injection check raise `ValueError: Header values must not contain newline characters`, returning a 500. `_local_redirect` now rejects targets containing control characters and falls back to the index page.*

### 修复 / Fixed

- **超长订阅地址被静默截断**：624 字符的地址被截成 500 字符入库，随后按这个「另一个地址」去抓取
  （实测返回 HTTP 404）。现在超过列宽直接拒绝并提示，不再截断。
- *Oversized feed URLs were silently truncated: a 624-character URL was stored as 500 characters and then fetched in that truncated form (observed as HTTP 404). Over-long URLs are now rejected with a message instead of truncated.*

- **超长文章链接被截断入库**：feed 里超过 1000 字符的链接会被截成一个无效地址存下来；
  现在跳过该条并记录日志。
- *Oversized article links were truncated: links longer than 1000 characters were stored truncated, producing an invalid URL; those entries are now skipped and logged.*

- **配置写错时报错不指明位置**：`server.port: "abc"` 之前只报
  `ValueError: invalid literal for int() with base 10: 'abc'`。现在抛 `ConfigError`，
  指明配置项、当前值与配置文件路径，并校验端口范围、超时/重试下限与日志级别。
- *Unclear errors on invalid configuration: `server.port: "abc"` used to produce only `ValueError: invalid literal for int() with base 10: 'abc'`. It now raises `ConfigError` naming the key, the offending value and the config file, and validates port ranges, timeout/retry minimums and the log level.*

- **用户可见提示没有本地化**：中文界面下仍然显示 `Feed URL is required.`，
  英文界面下反而出现硬编码的中文提示；现在所有提示都走 i18n。
- *User-facing messages were not localized: the Chinese UI still showed `Feed URL is required.` while the English UI showed hard-coded Chinese; every message now goes through i18n.*

- **把内部异常原文显示给用户**：抓取失败时提示里带着
*`HTTPConnectionPool(...) Max retries exceeded ... Errno 11001`。*
  现在只提示「失败，详见日志」，细节留在日志里。
- *Raw internal exceptions were shown to users: a failed fetch displayed `HTTPConnectionPool(...) Max retries exceeded ... Errno 11001`. Users now see "failed, see the log" while the details stay in the log.*

### 修复 · 上传时再次丢失的文件 / Fixed — Files lost again during upload

- 补回 `.dockerignore`（网页上传容易漏掉这类点文件）。
- *Restored `.dockerignore` (the web upload tends to skip dotfiles like this one).*
- 补回 `CHANGELOG.md`：v1.4 的提交（`Delete CHANGELOG.md`）把它删掉了。
- *Restored `CHANGELOG.md`: the v1.4 commit named `Delete CHANGELOG.md` removed it.*
- 删除 `app/.dockerfile`：网页上传无法删除文件，所以它一直残留。
- *Removed `app/.dockerfile`: the web upload cannot delete files, so it kept surviving.*
- 补回 `.gitignore` 中的运行时忽略项（`data/`、`*.db`、`*.sqlite3`、`*.log`、`logs/`）。
- *Restored the runtime ignore rules in `.gitignore` (`data/`, `*.db`, `*.sqlite3`, `*.log`, `logs/`).*
- **新增仓库布局断言**（`tests/test_v14_bugs.py`）：上列任一文件缺失、或 `app/.dockerfile` 仍然存在，
  测试就会失败，避免再次静默丢失。
- *Added repository-layout assertions (`tests/test_v14_bugs.py`): the suite fails if any of the files above is missing or if `app/.dockerfile` still exists, so this cannot regress silently again.*

> **提示**：GitHub 网页上传只能新增/覆盖文件，**不能删除文件**，也容易漏掉 `.dockerignore` 这类点文件。
> 建议改用 `git` 命令行提交（见 `提交到GitHub-说明.md`），提交后跑一次 `python tests/test_v14_bugs.py` 即可确认没有丢文件。
>
> **Note**: GitHub's web upload can only add or overwrite files — it **cannot delete** them, and it tends to skip dotfiles such as `.dockerignore`. Prefer committing with the `git` CLI (see `提交到GitHub-说明.md`), then run `python tests/test_v14_bugs.py` to confirm nothing was dropped.

### 测试 / Tests

| 套件 / Suite | 修复前 / Before | 修复后 / After |
|---|---|---|
| `tests/test_bugfixes.py`（第一轮回归，30 例 / first round, 30 cases） | 30 / 30 | 30 / 30 |
| `tests/test_v13_bugs.py`（第二轮，8 例 / second round, 8 cases） | 8 / 8 | 8 / 8 |
| `tests/test_v14_bugs.py`（本轮，7 例 / this round, 7 cases） | **0 / 7** | **7 / 7** |
| `tests/test_e2e_smoke.py`（端到端 / end-to-end） | 通过 / passed | 通过 / passed |
| `tests/test_debug_reloader.py`（进程级 / process-level） | 2 个调度器 / 2 schedulers | 1 个调度器 / 1 scheduler |

## [1.4.0] - 2026-10-07

第二轮审查版本：在 v1.3 基础上又修复 **8 项缺陷**，其中包含一个可绕过 SSRF 防护的安全漏洞。
Second audit release: **8 more bugs fixed** on top of v1.3, including a security hole that let SSRF protection be bypassed.

新增 8 个针对性用例与 1 个真实进程级验证脚本，全部通过。
*8 new targeted test cases and 1 real process-level verification script were added; all of them pass.*

### 安全 / Security

- **修复可绕过 SSRF 防护的重定向漏洞（高危）**：原来只校验订阅地址本身，而 `requests` 默认自动跟随重定向；
  一个公网 feed 只要返回 `302 Location: http://169.254.169.254/...` 就能让服务端去访问内网，防护形同虚设。
  现在改为不自动跟随重定向，逐跳校验后再跳（最多 5 跳），正文抽取路径同样处理。
- *Fixed a redirect hole that bypassed SSRF protection (high severity): only the feed URL itself was validated, while `requests` follows redirects by default; a public feed returning `302 Location: http://169.254.169.254/...` was enough to make the server reach the internal network, leaving the protection useless.*
*Redirects are no longer followed automatically: every hop is re-validated before it is taken (at most 5 hops), and the full-text extraction path is handled the same way.*

- **限制响应体大小**：恶意源可以返回几十 GB 响应，原来会被整份读入内存导致进程被打爆。
  新增 `fetch.max_bytes`（默认 8 MB）与 `fulltext.max_bytes`（默认 2 MB），超出即截断。
- *Response size is now bounded: a malicious feed could return tens of GB, and the whole body used to be read into memory, killing the process.*
*New `fetch.max_bytes` (8 MB by default) and `fulltext.max_bytes` (2 MB by default); anything larger is truncated.*

### 修复 / Fixed

- **调试模式 / 多进程下调度器重复启动**：`flask run --debug` 会派生父/子两个进程，两者都会执行引导逻辑；
  原来只判断配置里的 `server.debug`，而 `--debug` 时该值仍是 `false`，实测两个进程各起一个调度器（重复抓取、重复发信）。
  现在同时判断 `FLASK_DEBUG` 环境变量与 `app.debug`，并新增逐进程开关 `RSS_AGGREGATOR_SCHEDULER=off`（多 worker 部署时只让一个进程跑调度器）。
- *The scheduler started twice under debug mode / multiple processes: `flask run --debug` forks a parent and a child process, and both ran the bootstrap logic; the old check only looked at `server.debug`, which is still `false` under `--debug`, so both processes started a scheduler (duplicate fetching and duplicate emails).*
*The check now also considers the `FLASK_DEBUG` environment variable and `app.debug`, and a per-process switch `RSS_AGGREGATOR_SCHEDULER=off` was added (run the scheduler in only one process when deploying multiple workers).*

- **`/healthz` 不校验数据库**：原来无条件返回 `ok`，数据库或表结构损坏时容器仍被判定为健康、不会重启；现在会真的查询 `source` / `article` 表，失败返回 503。
- *`/healthz` did not check the database: it always returned `ok`, so a container with a broken database or schema was still reported healthy and never restarted; it now really queries the `source` and `article` tables and returns 503 on failure.*

- **`read` 为 NULL 的文章在「只看未读」里消失**：过滤条件只有 `read IS false`，历史数据或外部写入的 NULL 行会被静默隐藏；现在 NULL 与 false 一并视为未读。
- *Articles with `read = NULL` disappeared from "Unread only": the filter was `read IS false`, silently hiding NULL rows from legacy data or external writers; NULL is now treated as unread together with false.*

- **通知邮件列出并未入库的文章**：并发冲突导致部分文章被跳过时，邮件正文仍会列出全部候选条目；现在只列出真正写入数据库的文章。
- *Notification emails listed articles that were never stored: when a concurrency conflict skipped some entries, the email body still listed every candidate; it now lists only the articles actually written to the database.*

- **正文抽取按原始字节解析**：交给 BeautifulSoup 的是响应字节而非 `response.text`，由它按 meta/BOM 判断编码，避免 GBK 等页面正文乱码。
- *Full-text extraction parses raw bytes: BeautifulSoup receives the response bytes instead of `response.text`, letting it detect the encoding from meta tags or the BOM and avoiding mojibake on GBK and similar pages.*

### 修复 · 打包遗漏（v1.3 上传时丢失）/ Fixed — Packaging gaps (lost during the v1.3 upload)

- 补回 `.dockerignore`（缺失会把 `.git`、本地数据库打进镜像）。
- *Restored `.dockerignore` (without it, `.git` and local databases were baked into the image).*

- 补回 `.gitignore` 中的运行时忽略项（`data/`、`*.db`、`*.sqlite3`、`*.log`、`logs/`）。
- *Restored the runtime ignore rules in `.gitignore` (`data/`, `*.db`, `*.sqlite3`, `*.log`, `logs/`).*

- 删除 `app/.dockerfile`：它已被仓库根目录的 `Dockerfile` 取代，留着会让 `docker build` 的行为取决于使用哪一份，容易误构建。
- *Removed `app/.dockerfile`: it was superseded by the root `Dockerfile`, and keeping both made `docker build` depend on which one was used, inviting broken builds.*

- 补上 `tests/` 测试目录（v1.3 未包含）。
- *Restored the `tests/` directory (missing in v1.3).*

### 测试 / Tests

| 套件 / Suite | 结果 / Result |
|---|---|
| `tests/test_bugfixes.py`（第一轮回归，30 例 / first round, 30 cases） | 30 / 30 通过 / passed |
| `tests/test_v13_bugs.py`（本轮新增，8 例 / new this round, 8 cases） | 8 / 8 通过 / passed |
| `tests/test_e2e_smoke.py`（端到端 / end-to-end） | 21 / 21 通过 / passed |
| `tests/test_debug_reloader.py`（`flask run --debug` 进程级验证 / process-level check） | 修复前 2 个调度器 → 修复后 1 个 / 2 schedulers before → 1 after |

测试随仓库一起分发，只用标准库 `unittest.mock`，不引入额外依赖：
*Tests ship with the repository and use only the standard-library `unittest.mock`, adding no extra dependencies:*

```bash
python tests/test_bugfixes.py        # 第一轮 30 项回归 / first round, 30 regression cases
python tests/test_v13_bugs.py        # 本轮 8 项 / this round, 8 cases
python tests/test_e2e_smoke.py       # 端到端（会真的起一次服务）/ end-to-end (starts a real server)
python tests/test_debug_reloader.py  # 进程级：确认调度器只启动一次 / confirms the scheduler starts once
```

镜像里不包含 `tests/`（见 `.dockerignore`）。
*The image does not contain `tests/` (see `.dockerignore`).*

Dockerfile 在构建期额外做一次模块导入检查，确保镜像内所有模块都能正常加载。
*The Dockerfile additionally performs an import check at build time so that every module in the image loads correctly.*

### 配置新增 / Configuration

```yaml
fetch:
  max_bytes: 8388608       # feed 响应体上限（字节）/ max feed response size in bytes
fulltext:
  max_bytes: 2097152       # 文章页响应体上限（字节）/ max article page size in bytes
```

多进程部署时，除运行调度器的那个进程外，其余进程设置环境变量 `RSS_AGGREGATOR_SCHEDULER=off`。
*When deploying multiple processes, set `RSS_AGGREGATOR_SCHEDULER=off` in every process except the one that runs the scheduler.*

---

## [1.3.0] - 2026-10-07

本次为缺陷修复版本：共修复 **30 项缺陷**，其中 7 项导致程序在修复前**完全无法使用**。
Bug-fix release: **30 bugs fixed**, 7 of which made the application **completely unusable** before this release.

同时新增 30 个回归用例与 21 项端到端冒烟检查，全部通过。
*30 regression cases and 21 end-to-end smoke checks were added; all of them pass.*

### 修复 · 致命（修复前程序不可用）/ Fixed — Critical (the app was unusable)

- **`create_app()` 必然崩溃，应用无法启动**：`app/config.py` 的 `_load_config()` 把配置写在实例属性上，而 `get()` / `to_flask_config()` 读的是类属性（恒为 `None`），导致 `to_flask_config()` 抛 `TypeError: argument of type 'NoneType' is not iterable`。
- *`create_app()` always crashed, so the app could not start: `_load_config()` in `app/config.py` stored the config on an instance attribute while `get()` and `to_flask_config()` read the class attribute (always `None`), making `to_flask_config()` raise `TypeError: argument of type 'NoneType' is not iterable`.*

- **`ConfigLoader.get()` 恒返回默认值**：同上的类/实例属性错位；异常被 `except (KeyError, TypeError)` 静默吞掉，所有通过它读取的配置都失效。
- *`ConfigLoader.get()` always returned the default value: the same class/instance attribute mismatch; the exception was silently swallowed by `except (KeyError, TypeError)`, so every setting read through it was lost.*

- **每个页面都返回 500**：模板位于 `app/web/templates/`，但蓝图未声明 `template_folder`，Jinja 只会在 `app/templates/` 查找 → `TemplateNotFound: index.html`。
- *Every page returned 500: the templates live in `app/web/templates/`, but the blueprint did not declare `template_folder`, so Jinja only looked in `app/templates/` → `TemplateNotFound: index.html`.*

- **`flash()` 一律 500**：从未设置 `SECRET_KEY`，`RuntimeError: The session is unavailable because no secret key was set`，表现为「添加源」写库成功却返回 500。
- *`flash()` always returned 500: `SECRET_KEY` was never set, giving `RuntimeError: The session is unavailable because no secret key was set`; adding a source wrote to the database but still answered 500.*

- **定时抓取 100% 失败**：`fetch_source` 被直接交给 APScheduler，而应用上下文只在注册任务时推入；任务在调度线程执行时 `Source.query` 抛 `RuntimeError: Working outside of application context`。
- *Scheduled fetching failed 100% of the time: `fetch_source` was handed straight to APScheduler while the application context was only pushed while registering jobs; when the task ran on the scheduler thread, `Source.query` raised `RuntimeError: Working outside of application context`.*

- **OPML 导入必然失败**：使用了 lxml 专有的 `outline.getparent()`，而模块导入的是标准库 `xml.etree.ElementTree`，异常被宽泛捕获后恒返回 `(0, 1)`。
- *OPML import always failed: it called `outline.getparent()`, which is lxml-specific, while the module imported the standard-library `xml.etree.ElementTree`; the broad `except` swallowed the error and it always returned `(0, 1)`.*

- **抓取失败不回滚**：失败分支只记日志便返回，已 `add` 未提交的文章残留在 session 中，会被之后任意一次 `commit()` 悄悄写进数据库。
- *A failed fetch did not roll back: the failure branch only logged and returned, leaving added-but-uncommitted articles in the session to be silently written by any later `commit()`.*

### 修复 · 安全 / Fixed — Security

- **新增 SSRF 防护**（`app/urlsafety.py`）：只允许 http/https，拒绝回环、私有、链路本地与保留地址；已接入抓取、正文抽取、OPML 导入、新增源四条路径。纯内网场景可用 `security.allow_private_networks` 放开。
- *Added SSRF protection (`app/urlsafety.py`): only http/https is allowed, and loopback, private, link-local and reserved addresses are rejected; it is wired into fetching, full-text extraction, OPML import and adding a source. For internal-only setups it can be relaxed with `security.allow_private_networks`.*

- **改状态的接口不再接受 GET**：`delete_source` / `refresh_source` 由 GET 改为 POST（原来浏览器预取、`<img>` 标签或爬虫即可删库）。
- *State-changing endpoints no longer accept GET: `delete_source` and `refresh_source` moved from GET to POST (previously browser prefetching, an `<img>` tag or a crawler could delete data).*

- **新增跨站请求来源校验**（`app/security.py`）：POST 的 `Origin`/`Referer` 与本站不一致直接 403，可通过 `security.csrf_origin_check` 关闭。
- *Added a cross-site origin check (`app/security.py`): a POST whose `Origin`/`Referer` does not match this site is rejected with 403; it can be disabled with `security.csrf_origin_check`.*

- **转义 LIKE 通配符**：原来用户输入 `%` 会退化成全表匹配，`_` 会匹配任意单字符。
- *Escaped LIKE wildcards: a user-supplied `%` used to degrade into a full-table match, and `_` matched any single character.*

### 修复 · 数据正确性 / Fixed — Data correctness

- **`Article.link` 增加唯一索引**：原来只靠「先查再插」去重，手动刷新（请求线程）与定时任务（调度线程）并发时会重复入库。
- *Added a unique index on `Article.link`: de-duplication relied on check-then-insert, so a manual refresh (request thread) racing a scheduled job (scheduler thread) inserted duplicates.*

- **并发冲突降级重试**：批量提交撞唯一约束时改为逐条 savepoint 重试，不再整批失败。
- *Graceful retry on concurrency conflicts: when a bulk commit hits the unique constraint it now falls back to per-row savepoint retries instead of failing the whole batch.*

- **新增 `app/schema.py`**：为已有数据库幂等补索引，并先清理历史重复文章（同一 `link` 保留 id 最小的一条），删除条数写入日志。
- *Added `app/schema.py`: it idempotently backfills indexes on an existing database and first removes historical duplicate articles (keeping the lowest id per `link`), logging how many rows were deleted.*

- **去重查询由 N+1 改为一次批量查询**（按 400 分批，避开 SQLite 变量上限）。
- *De-duplication switched from N+1 queries to one batched query (chunked by 400 to stay under the SQLite variable limit).*

### 修复 · 健壮性与性能 / Fixed — Robustness and performance

- **新增/导入的源立即注册调度任务**（原来必须重启才会自动更新）。
- *Newly added or imported sources are scheduled immediately (previously automatic updates only began after a restart).*

- **全文抽取加配额与超时**：原来每个没有 `content` 的条目都同步抓一次网页，单次请求最坏可阻塞 50 × 10 秒；现由 `fulltext.enabled` / `max_per_fetch` / `timeout` 控制。
- *Full-text extraction gained a quota and timeouts: it used to fetch one web page synchronously per entry lacking `content`, blocking a single request for up to 50 × 10 seconds; it is now governed by `fulltext.enabled` / `max_per_fetch` / `timeout`.*

- **`interval` 容错解析并强制 ≥ 5 分钟**（原来非数字直接 500，0/负数注册非法任务）。
- *`interval` is parsed tolerantly and clamped to at least 5 minutes (a non-numeric value used to cause a 500, and 0 or negative values registered invalid jobs).*

- **重复 URL 友好提示**，不再抛 `IntegrityError` 500。
- *Duplicate URLs now produce a friendly message instead of an `IntegrityError` 500.*

- **分页链接保留查询条件**：原来用 `request.view_args`，把 `q` / `source_id` / `unread` / `lang` 全丢掉，搜索翻到第二页会变成全量列表。
- *Pagination links keep the query conditions: they used `request.view_args`, dropping `q` / `source_id` / `unread` / `lang`, so page two of a search turned into the full list.*

- **`unread` 过滤形成闭环**：新增「标记已读 / 标记未读」接口与按钮（原来 `Article.read` 永远是 `False`）。
- *The `unread` filter became a closed loop: "mark as read" and "mark as unread" endpoints and buttons were added (previously `Article.read` was always `False`).*

- **`init_scheduler` 尊重 `scheduler.enabled`**，并在调试重载器父进程中不启动，避免起两个调度器重复抓取。
- *`init_scheduler` honours `scheduler.enabled` and no longer starts in the debug reloader's parent process, avoiding two schedulers fetching twice.*

- **引导逻辑移出 `__main__` 分支**：用 gunicorn/uwsgi（`main:app`）或 `flask run` 启动时同样会建表（原来首个请求报 no such table）。
- *Bootstrap logic moved out of the `__main__` block: starting with gunicorn/uwsgi (`main:app`) or `flask run` now creates the tables too (the first request used to fail with "no such table").*

- **日志只配置一次**：支持绝对路径与 UTF-8，并让 `app.logger` 只做透传，消除每条日志被输出两次的问题。
- *Logging is configured once: it supports absolute paths and UTF-8, and `app.logger` now only propagates, eliminating every log line being printed twice.*

- **调度器实例移入 `app/scheduler.py`**：同名子模块会把包属性 `app.scheduler` 覆盖成模块对象，导致 `from app import scheduler` 拿到模块而非实例。
- *The scheduler instance moved into `app/scheduler.py`: the same-named submodule overwrote the package attribute `app.scheduler` with a module object, so `from app import scheduler` yielded a module instead of the instance.*

- **邮件通知支持多收件人**（逗号/分号分隔）、显式 `ehlo()`、配置不全时明确报错。
- *Email notifications support multiple recipients (comma or semicolon separated), call `ehlo()` explicitly, and report a clear error when the configuration is incomplete.*

- **时间统一为 naive UTC**，避开 Python 3.12 起废弃的 `datetime.utcnow()`。
- *Timestamps are uniformly naive UTC, avoiding `datetime.utcnow()`, deprecated since Python 3.12.*

- **页面显示 `app.name`**：原来模板只取 i18n 字典，配置里的应用名配了也不会显示。
- *The page shows `app.name`: the template only read the i18n dictionary, so the application name from the config never appeared.*

### 修复 · 部署与配置 / Fixed — Deployment and configuration

- **`Dockerfile` 移到仓库根目录**：原来位于 `app/.dockerfile`，而 compose 使用 `build: .`，`docker compose build` 必然失败。
- *`Dockerfile` moved to the repository root: it lived at `app/.dockerfile` while compose uses `build: .`, so `docker compose build` always failed.*

- **数据库不再随容器重启丢失**：`sqlite:///rss.db` 是相对路径，Flask-SQLAlchemy 3.x 会解析到 instance 目录（`/app/instance/rss.db`），而 compose 只挂载了 `./data:/app/data`；现改为 `sqlite:///data/rss.db`，并在代码中把相对 SQLite 路径锚定到项目根目录（容器内即 `/app/data`）。
- *The database no longer disappears when the container restarts: `sqlite:///rss.db` is a relative path that Flask-SQLAlchemy 3.x resolves into the instance folder (`/app/instance/rss.db`), while compose only mounts `./data:/app/data`; it is now `sqlite:///data/rss.db`, and the code anchors relative SQLite paths to the project root (i.e. `/app/data` inside the container).*

- **新增 `.dockerignore`**：避免把 `.git`、本地数据库、`__pycache__` 打进镜像。
- *Added `.dockerignore`: keeps `.git`, local databases and `__pycache__` out of the image.*

- **`docker-compose.yaml`**：移除已废弃的 `version` 字段，补充时区环境变量。
- *`docker-compose.yaml`: removed the obsolete `version` field and added a timezone environment variable.*

- **修正 README 与仓库不一致之处**：补上缺失的 `CHANGELOG.md`，改正 `docker-compose.yml` → `docker-compose.yaml`，更新项目结构与配置示例，并显著提示「本项目无鉴权，请勿直接暴露公网」。
- *Fixed README inconsistencies: added the missing `CHANGELOG.md`, corrected `docker-compose.yml` to `docker-compose.yaml`, updated the project structure and configuration examples, and made the "this project has no authentication, do not expose it publicly" warning prominent.*

### 新增 / Added

- `app/urlsafety.py` — 出站 URL 安全校验（SSRF 防护）。
- *`app/urlsafety.py` — outbound URL safety checks (SSRF protection).*

- `app/security.py` — 跨站请求来源校验。
- *`app/security.py` — cross-site request origin check.*

- `app/schema.py` — 建表、幂等补索引、清理重复数据。
- *`app/schema.py` — table creation, idempotent index backfill, duplicate cleanup.*

- `Dockerfile`（仓库根目录）、`.dockerignore`。
- *`Dockerfile` (repository root) and `.dockerignore`.*

- `/healthz` 健康检查路由，以及容器 `HEALTHCHECK`。
- *The `/healthz` health-check route and a container `HEALTHCHECK`.*

- `tests/test_bugfixes.py` — 30 个回归用例，`python tests/test_bugfixes.py` 直接运行。
- *`tests/test_bugfixes.py` — 30 regression cases, run directly with `python tests/test_bugfixes.py`.*

- `tests/test_e2e_smoke.py` — 端到端冒烟：真启动 `main.py`、真发 HTTP、真抓本地 RSS 源。
- *`tests/test_e2e_smoke.py` — end-to-end smoke test: really starts `main.py`, really sends HTTP, really fetches a local RSS feed.*

### 变更（可能影响已有部署）/ Changed (may affect existing deployments)

- **数据库默认路径**：`sqlite:///rss.db` → `sqlite:///data/rss.db`。升级时请把旧库文件移动到 `data/rss.db`，或在 `config.yaml` 中写回原路径。
- *Default database path: `sqlite:///rss.db` → `sqlite:///data/rss.db`. When upgrading, move the old database file to `data/rss.db` or point `config.yaml` back at the original path.*

- **接口方法**：删除源、刷新源、标记已读/未读改为 **POST**。
- *Endpoint methods: deleting a source, refreshing a source, and marking read/unread are now POST.*

- **默认拒绝内网抓取**：需要订阅内网源时设置 `security.allow_private_networks: true`。
- *Internal addresses are rejected by default: set `security.allow_private_networks: true` if you subscribe to internal feeds.*

- **全文抽取默认每次最多 5 篇**：需要更多请调大 `fulltext.max_per_fetch`。
- *Full-text extraction is capped at 5 articles per run by default: raise `fulltext.max_per_fetch` for more.*

- **新增配置段**：`fetch`、`fulltext`、`security`、`logging`，以及 `app.secret_key`（可选）。
- *New configuration sections: `fetch`, `fulltext`, `security`, `logging`, plus the optional `app.secret_key`.*

### 测试 / Tests

| 套件 / Suite | 结果 / Result |
|---|---|
| `tests/test_bugfixes.py`（回归 / regression） | 30 / 30 通过 / passed |
| `tests/test_e2e_smoke.py`（端到端 / end-to-end） | 21 / 21 通过 / passed |

修复前后对比（同一套回归用例）：
*Before-and-after comparison (the same regression suite):*

| 阶段 / Stage | 结果 / Result |
|---|---|
| 原始代码 / original code | 0 / 27 失败 / failing（全部被「应用无法启动」挡住 / all blocked by "the app cannot start"） |
| 仅修 `create_app` / only `create_app` fixed | 10 通过 / 19 失败 / 10 passing, 19 failing（其余缺陷逐条暴露 / remaining bugs exposed one by one） |
| 全部修复后 / all fixes applied | 30 / 30 通过 / passed |

---

## [1.2.0] 及更早 / and earlier

v1.2 及更早的版本没有 `CHANGELOG.md`，变更记录见各版本的提交历史与 README。
*v1.2 and earlier shipped without a `CHANGELOG.md`; see the commit history and README of each release instead.*
