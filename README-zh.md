# RSS 聚合器

[English](README.md) | [中文](README-zh.md)

**当前版本：2.1.1** &nbsp;·&nbsp; `/healthz` 也会返回版本号。

一个功能丰富、可自托管的 RSS 订阅聚合器。支持定时抓取、OPML 导入导出、全文提取、关键词搜索过滤、邮件通知，并提供中英文双语 Web 界面。适合个人或团队用于信息聚合、内容监控和阅读管理。

---

## ✨ 功能特性

### 核心功能

- **RSS 源管理**：添加、删除、编辑、暂停 RSS 源，支持分类（如“科技”“新闻”“博客”）。
- **定时抓取**：基于 APScheduler，每个源可独立设置更新间隔（最低 5 分钟）；已暂停的源不会抓取。
- **登录保护**：单用户登录，口令只存哈希；会话 Cookie 加固（`HttpOnly`、`SameSite=Lax`），连续失败会临时锁定。未配置口令时不启用。
- **数据持久化**：使用 SQLite（默认）或 PostgreSQL，通过 SQLAlchemy 实现；老库升级会就地补列补索引。
- **Web 界面**：基于 Flask 的响应式界面，支持中英文切换，并显示全局与每个源的未读数。
- **文章阅读**：展示标题、摘要、发布时间，点击跳转原文。

### 高级功能

- **文章保留策略**：按每源条数或天数上限自动裁剪，长期运行不会无限膨胀。
- **OPML 导入/导出**：一键迁移订阅源，兼容主流 RSS 阅读器。
- **全文提取**：对只提供摘要的源，尝试自动抓取网页正文（基于 BeautifulSoup）。
- **关键词过滤与全文搜索**：支持按标题、摘要、正文搜索，可结合源分类和未读状态过滤。
- **自定义更新频率**：每个源可单独配置抓取间隔，灵活控制资源占用。
- **任务可见**：后台抓取会被登记，并通过 `GET /api/status` 暴露；网页每 5 秒轮询一次，显示正在抓取什么、抓完自动刷新。
- **跨平台**：Windows、macOS、Linux 都做了适配——按平台惯例存放数据、Windows 终端编码处理。
- **阅读视图**：每篇文章都有独立的沉浸式阅读页，排版舒适，带阅读进度条、预估阅读时长与上一篇/下一篇导航。
- **键盘快捷键**：`j` / `k` 在列表中移动、`o` 打开、`s` 收藏、`m` 标记已读、`/` 聚焦搜索、`?` 显示快捷键表。
- **分类导航**：一键按订阅源分类筛选列表。
- **全部标为已读**：一次清空积压，也可以在控制台里只清某个源。
- **即时反馈**：收藏用 Toast 提示而不是整页跳转，未读数同时显示在浏览器标签页标题上。
- **搜索高亮**：命中的关键词在标题与摘要里标出来；文本先转义再标记，feed 内容无法注入标记。
- **文章缩略图**：自动提取正文里的第一张图片作为懒加载缩略图，列表有了视觉锚点。
- **统计页**：`/stats` 展示总量、最近 14 天活跃度柱状图（纯 CSS 绘制）与每个源的已读比例。
- **命令面板**：按 `Ctrl`/`Cmd` + `K` 用键盘跳到任意文章、订阅源或页面。
- **视觉升级**：柔和渐变光晕、毛玻璃吸顶页头、卡片悬停上浮、抓取时的骨架占位。
- **无限滚动**：滚到底部自动加载下一页，片段由服务端渲染，与首屏样式完全一致。
- **即时搜索**：输入两个字符即出现下拉匹配结果，点击直达阅读页。
- **SSE 实时更新**：`GET /api/events` 推送任务进展与未读数，页头有连接指示灯，断线自动退回轮询。
- **JSON 接口**：`GET /api/articles` 提供带全部筛选条件的分页数据，便于脚本与集成使用。
- **侧栏工作区**：视图（全部 / 未读 / 收藏 / 今天）与订阅源并列，未读数实时显示，取代旧版顶部导航。
- **批量操作**：勾选文章后可一次标记已读、未读、收藏或删除。
- **外观可调**：强调色与列表密度可在侧栏切换，并按浏览器记住。
- **单一 JS 模块**：全部交互行为集中在 `app/static/app.js`，模板保持声明式。
- **语言切换**：每个页面都有可见的 中文 / English 切换，选择会记在 cookie 里。
- **控件完全本地化**：文件上传按钮由程序自己绘制，跟随界面语言而不是浏览器语言。
- **排序**：可按最新、最早或按订阅源排列。
- **关键词屏蔽**：用逗号分隔的关键词隐藏噪音条目，可与搜索及其它筛选组合使用。
- **视图各有身份**：在全部 / 未读 / 收藏 / 今天之间切换时，整块工作区的图标、标题、说明、计数与色调都会随之改变。
- **筛选标签**：每个生效的筛选条件都会显示为可点掉的标签，当前上下文一眼可见。
- **浅色与深色主题**：网页自动跟随系统深浅色，也可以手动切换；选择会按浏览器记住。
- **收藏**：任意文章可加星，并按「只看收藏」过滤，网页上一键完成。
- **数据导出**：文章可导出为 JSON（完整备份）、CSV（Excel 可直接打开）或 Markdown（阅读归档）。
- **网页控制台**：`/console` 提供紧凑的仪表盘与批量操作（全部抓取、暂停/恢复、编辑、删除），适合用浏览器时使用。
- **邮件推送通知**：配置 SMTP 后，抓取到新文章可自动发送邮件提醒。
- **Docker 部署**：提供 Dockerfile 和 docker-compose.yaml，方便快速部署。

---

## 🛠 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | Flask |
| 数据库 | SQLite / PostgreSQL（通过 SQLAlchemy） |
| RSS 解析 | feedparser |
| 定时任务 | APScheduler |
| 全文提取 | BeautifulSoup4 + requests |
| 邮件通知 | smtplib |
| 国际化 | 自定义 i18n 模块 |
| 部署 | Docker / Docker Compose |

---

## 📁 项目结构

```
rss_aggregator/
├── app/
│   ├── __init__.py          # 应用初始化
│   ├── config.py            # 配置加载器
│   ├── models.py            # ORM 模型（Source、Article）
│   ├── schema.py            # 建表、为老库补列补索引
│   ├── version.py           # 版本号唯一来源
│   ├── fetcher.py           # RSS 抓取与重试逻辑
│   ├── scheduler.py         # 后台调度器
│   ├── urlsafety.py         # 出站 URL 安全校验（防 SSRF）
│   ├── security.py          # 跨站请求来源校验
│   ├── auth.py              # 单用户登录、会话与失败锁定
│   ├── retention.py         # 文章保留策略（每源条数 / 天数）
│   ├── tasks.py             # 后台任务登记表（供 /api/status 使用）
│   ├── services.py          # 网页与控制台共用的业务操作
│   ├── reader.py            # 阅读视图辅助（纯文本、分段、阅读时长）
│   ├── highlight.py         # 搜索关键词安全高亮
│   ├── stats.py             # 统计页的聚合查询
│   ├── web/api_live.py      # 分页接口（JSON/HTML）与 SSE 推送
│   ├── exporter.py          # 文章导出：JSON / CSV / Markdown
│   ├── platform_utils.py    # Windows / macOS / Linux 差异集中处理
│   ├── i18n.py              # 中英文翻译
│   ├── opml.py              # OPML 导入导出
│   ├── fulltext.py          # 全文提取
│   ├── search.py            # 搜索过滤
│   ├── notifications.py     # 邮件通知
│   ├── static/
│   │   ├── app.css          # 设计系统：令牌、主题、密度、组件
│   │   ├── app.js           # 全部交互行为（命令面板、滚动、SSE、批量）
│   │   └── theme.js         # 浅色/深色主题切换（加载不闪白屏）
│   └── web/
│       ├── __init__.py
│       ├── routes.py        # 路由与视图
│       └── templates/
│           ├── base.html    # 公共布局（页头、主题切换、轮询）
│           ├── index.html   # 主页面：列表、搜索、导出
│           ├── reader.html  # 阅读视图（进度条、快捷键）
│           ├── stats.html   # 统计页（柱状图、按源分布）
│           ├── login.html   # 登录页模板
│           └── console.html # 网页控制台（仪表盘 + 批量操作）
├── tests/
│   ├── test_bugfixes.py       # 回归测试（30 例）
│   ├── test_v13_bugs.py       # v1.3 审查用例（8 例）
│   ├── test_v14_bugs.py       # v1.4 审查用例 + 仓库布局检查（7 例）
│   ├── test_v2_bugs.py        # v2.0.1 功能与修复用例（16 例）
│   ├── test_v202_bugs.py      # v2.0.2 任务接口、轮询与修复用例（14 例）
│   ├── test_v204_bugs.py      # v2.0.4 控制台与共享业务层用例（10 例）
│   ├── test_v205_bugs.py      # v2.0.5 界面、收藏、导出与跨平台用例（17 例）
│   ├── test_v206_bugs.py      # v2.0.6 阅读视图与 GUI 移除用例（17 例）
│   ├── test_v207_bugs.py      # v2.0.7 高亮、缩略图、统计与视觉用例（17 例）
│   ├── test_v208_bugs.py      # v2.0.8 分页接口、SSE、无限滚动与修复用例（15 例）
│   ├── test_v209_bugs.py      # v2.0.9 侧栏视图、批量操作与外观用例（14 例）
│   ├── test_v2091_palette.py  # v2.0.9 回归：命令面板必须能关闭（5 例）
│   ├── test_v210_bugs.py      # v2.1.0 语言切换、文件选择、排序与屏蔽（16 例）
│   ├── test_v211_bugs.py      # v2.1.1 视图身份、筛选标签与仓库卫生（12 例）
│   ├── test_e2e_smoke.py      # 端到端冒烟测试（会真的启动服务）
│   └── test_debug_reloader.py # 验证 --debug 下调度器只启动一次
├── main.py                  # 网页版入口
├── config.yaml              # 配置文件
├── requirements.txt         # Python 依赖
├── Dockerfile               # 必须留在构建上下文根目录（compose 用 `build: .`）
├── docker-compose.yaml
├── CHANGELOG.md             # 版本历史
├── .dockerignore
├── .gitignore
└── README.md                # 本文件（英文版）
```

> **安全提示**：自 2.0.1 起内置可选的单用户登录。若要在自有网络之外访问，
> 请先配置 `auth.password_hash`（或环境变量 `RSS_AGGREGATOR_PASSWORD`）；
> 未配置口令时，任何能访问到该端口的人都能直接使用界面。

---

## 🚀 快速开始

### 环境要求

- Python 3.9 或更高版本
- pip
- （可选）Docker 和 Docker Compose

### 本地运行

1. **克隆仓库**
   ```bash
   git clone https://github.com/forestwolf-ai/RSS-Aggregator.git
   cd RSS-Aggregator
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **配置**
   复制并编辑 `config.yaml`，根据需要设置语言、数据库、邮件等（见[配置说明](#配置说明)）。

4. **启动应用**
   ```bash
   python main.py
   ```

5. **访问界面**
   打开浏览器访问 [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## ⚙️ 配置说明

配置文件为 `config.yaml`，示例及注释如下：

```yaml
app:
  name: "RSS Aggregator"       # 应用名称（显示在页面上）
  language: "zh"               # 默认语言：en 或 zh
  timezone: "Asia/Shanghai"    # 调度器时区
  # secret_key: "..."          # 可选；不配置则每次启动随机生成

database:
  # 相对 SQLite 路径锚定到项目根目录：本地是 <项目>/data/rss.db，
  # 容器内是 /app/data/rss.db（即被挂载的数据卷）
  url: "sqlite:///data/rss.db"
  # PostgreSQL 示例：postgresql://user:password@localhost/dbname

scheduler:
  enabled: true                # 是否启用自动抓取
  default_interval: 30         # 默认更新间隔（分钟，最小 5）

server:
  host: "0.0.0.0"              # 监听地址
  port: 5000
  debug: false

auth:
  enabled: true                # 未配置口令时自动失效
  username: "admin"
  # password_hash: "..."       # 生成：python -m app.auth <你的口令>
  # password: "明文口令"        # 仅为方便试用，推荐用哈希
  session_days: 14

retention:
  max_articles_per_source: 0   # 每个源最多保留多少条（0 = 不限）
  max_age_days: 0              # 只保留最近多少天（0 = 不限）

tasks:
  max_concurrent: 5            # 后台抓取并发上限；满了以后「添加源」退回同步抓取

fetch:
  retries: 3                   # 网络类错误的重试次数
  timeout: 15                  # 单次请求超时（秒）
  max_entries: 50              # 每次抓取最多「新增」多少条，其余下次补齐
  max_bytes: 8388608           # feed 响应体上限（字节，8 MB），超出即截断
  initial_async: true          # 添加源后立即返回，首次抓取转后台

fulltext:
  enabled: true
  max_per_fetch: 5             # 每次最多为几篇文章抓正文（0 = 关闭）
  timeout: 10
  max_bytes: 2097152           # 文章页响应体上限（字节，2 MB）

security:
  allow_private_networks: false  # 保持 false 可阻止抓取内网地址（防 SSRF）
  csrf_origin_check: true        # 拒绝跨站 POST（Origin/Referer 校验）
  session_cookie_secure: false   # 走 HTTPS 时设为 true
  max_content_bytes: 8388608     # 超过此大小的请求体返回 413

logging:
  level: "INFO"
  file: "rss_aggregator.log"

notifications:
  email:
    enabled: false             # 是否启用邮件通知
    smtp_server: "smtp.example.com"
    smtp_port: 587
    use_tls: true
    username: "user@example.com"
    password: "password"
    from_addr: "user@example.com"
    to_addr: "user@example.com"  # 多个收件人用英文逗号分隔
```

---

## 🧩 使用说明

### 添加 RSS 源

在首页表单中输入：
- **名称**：便于识别的源名称。
- **URL**：RSS 或 Atom 地址。
- **分类**：可选，如“新闻”“科技”。
- **更新间隔**：自动抓取间隔（分钟，最低 5）。

提交后立即抓取一次，随后按间隔自动更新。

### 搜索与过滤

使用页面顶部的搜索框：
- 输入关键词，搜索范围包括标题、摘要和正文。
- 可选按特定源过滤。
- 勾选“仅未读”只查看未读文章。

### OPML 导入导出

- **导出**：点击“导出 OPML”下载包含所有源的 XML 文件。
- **导入**：点击“导入 OPML”选择文件上传，源会自动添加并保留分类。

### 邮件通知

要接收新文章邮件提醒：
1. 在 `config.yaml` 中将 `notifications.email.enabled` 设为 `true`。
2. 填写 SMTP 服务器信息及收件地址。
3. 当源被抓取（手动或定时）并发现新文章时，系统自动发送邮件。

---

## 🐳 Docker 部署

### 使用 Docker Compose

```bash
docker compose up -d --build
```

该命令会构建镜像并后台启动容器。应用将在 `http://localhost:5000` 可用。

### 数据持久化

SQLite 数据库存储在 `./data` 卷中，配置文件以只读方式挂载。如需自定义设置，请先编辑 `config.yaml`。

### 自定义配置

如需覆盖默认 compose 文件，可自行修改：

```yaml
services:
  rss:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data
    restart: unless-stopped
```

---

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。请保持代码风格一致，并在本地测试。

---

## 📞 联系方式

- GitHub Issues：https://github.com/forestwolf-ai/RSS-Aggregator/issues
