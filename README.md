# RSS Aggregator

[English](README.md) | [中文](README-zh.md)

**Current version: 2.0.3** &nbsp;·&nbsp; `/healthz` reports it too.

A feature-rich, self-hosted RSS feed aggregator with scheduled fetching, OPML import/export, full-text extraction, search, email notifications, and a bilingual web interface. Ideal for personal or team use in information aggregation, content monitoring, and reading management.

---

## ✨ Features

### Core Features

- **RSS Source Management**: Add, delete, edit and pause RSS feeds with categories (e.g., "Tech", "News", "Blogs").
- **Scheduled Fetching**: Powered by APScheduler, each source can have its own update interval (minimum 5 minutes); paused sources are skipped.
- **Login protection**: Single-user sign-in with hashed passwords, session cookies hardened (`HttpOnly`, `SameSite=Lax`) and a lockout after repeated failures. Off by default until you set a password.
- **Persistent Storage**: Uses SQLite (default) or PostgreSQL via SQLAlchemy, with lightweight in-place migrations for existing databases.
- **Web Interface**: Flask-based responsive UI with English/Chinese language switching, plus global and per-feed unread counters.
- **Article Reading**: Displays title, summary, publish time, and links to the original article.

### Advanced Features

- **Retention policy**: Cap articles per feed or by age so a long-running instance does not grow without bound.
- **OPML Import/Export**: One-click migration of feed subscriptions, compatible with mainstream RSS readers.
- **Full-Text Extraction**: For sources that provide only summaries, attempts to extract the full article text using BeautifulSoup.
- **Keyword Filtering & Full-Text Search**: Search across title, summary, and content, with optional filtering by source and unread status.
- **Custom Update Frequency**: Each source can have its own fetch interval.
- **Email Notifications**: Configure SMTP to receive email alerts when new articles are fetched.
- **Docker Deployment**: Dockerfile and docker-compose.yaml provided for easy deployment.

---

## 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| Web Framework | Flask |
| Database | SQLite / PostgreSQL (through SQLAlchemy) |
| RSS Parsing | feedparser |
| Scheduled Tasks | APScheduler |
| Full-Text Extraction | BeautifulSoup4 + requests |
| Email Notifications | smtplib |
| Internationalization | Custom i18n module |
| Deployment | Docker / Docker Compose |

---

## 📁 Project Structure

```
rss_aggregator/
├── app/
│   ├── __init__.py          # Application initialization
│   ├── config.py            # Configuration loader
│   ├── models.py            # ORM models (Source, Article)
│   ├── schema.py            # Create tables, backfill columns/indexes for existing databases
│   ├── version.py           # Single source of truth for the version number
│   ├── fetcher.py           # RSS fetching with retry logic
│   ├── scheduler.py         # Background scheduler
│   ├── urlsafety.py         # SSRF guard for outbound URLs
│   ├── security.py          # Cross-site request origin check
│   ├── auth.py              # Single-user login, session and lockout
│   ├── retention.py         # Article retention (per-feed count / age)
│   ├── i18n.py              # English/Chinese translations
│   ├── opml.py              # OPML import/export
│   ├── fulltext.py          # Full-text extraction
│   ├── search.py            # Search and filter
│   ├── notifications.py     # Email notifications
│   └── web/
│       ├── __init__.py
│       ├── routes.py        # Routes and views
│       └── templates/
│           ├── index.html   # Main page template
│           └── login.html   # Sign-in page
├── tests/
│   ├── test_bugfixes.py       # Regression tests (30 cases)
│   ├── test_v13_bugs.py       # v1.3 audit tests (8 cases)
│   ├── test_v14_bugs.py       # v1.4 audit tests + repository layout checks (7 cases)
│   ├── test_v2_bugs.py        # v2.0.1 features and fixes (16 cases)
│   ├── test_e2e_smoke.py      # End-to-end smoke test (starts a real server)
│   └── test_debug_reloader.py # Verifies the scheduler starts only once under --debug
├── main.py                  # Entry point
├── config.yaml              # Configuration file
├── requirements.txt         # Python dependencies
├── Dockerfile               # Must stay at the build context root (compose uses `build: .`)
├── docker-compose.yaml
├── CHANGELOG.md             # Version history
├── .dockerignore
├── .gitignore
└── README.md                # This file
```

> **Security note**: since 2.0.1 the app ships with optional single-user authentication.
> Set `auth.password_hash` (or the `RSS_AGGREGATOR_PASSWORD` environment variable) before
> exposing it beyond your own network — without a password the UI is open to anyone who can reach it.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.9 or higher
- pip
- (Optional) Docker and Docker Compose

### Local Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/forestwolf-ai/RSS-Aggregator.git
   cd RSS-Aggregator
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure**
   Copy `config.yaml` and edit it according to your needs (see [Configuration](#configuration)).

4. **Run the application**
   ```bash
   python main.py
   ```

5. **Open the web interface**
   Visit [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## ⚙️ Configuration

The configuration file is `config.yaml`. Below is a sample with comments:

```yaml
app:
  name: "RSS Aggregator"       # Application name (shown in the UI)
  language: "en"               # Default language: en or zh
  timezone: "Asia/Shanghai"    # Timezone for scheduler
  # secret_key: "..."          # Optional; a random key is generated per start if absent

database:
  # Relative SQLite paths are anchored to the project root, so this file is
  # <project>/data/rss.db locally and /app/data/rss.db in the container (mounted volume).
  url: "sqlite:///data/rss.db"
  # Example PostgreSQL: postgresql://user:password@localhost/dbname

scheduler:
  enabled: true                # Enable/disable automatic fetching
  default_interval: 30         # Default update interval (minutes, minimum 5)

server:
  host: "0.0.0.0"              # Listen address
  port: 5000
  debug: false

auth:
  enabled: true                # Ignored (off) until a password is configured
  username: "admin"
  # password_hash: "..."       # Generate with: python -m app.auth <password>
  # password: "plain"          # Convenience only; a hash is recommended
  session_days: 14

retention:
  max_articles_per_source: 0   # Keep at most N articles per feed (0 = unlimited)
  max_age_days: 0              # Keep articles newer than N days (0 = unlimited)

fetch:
  retries: 3                   # Retries for network errors
  timeout: 15                  # Per-request timeout (seconds)
  max_entries: 50              # Max NEW articles stored per run (the rest are picked up later)
  max_bytes: 8388608           # Max feed response size in bytes (8 MB); larger is truncated
  initial_async: true          # Return immediately and fetch the new feed in the background

fulltext:
  enabled: true
  max_per_fetch: 5             # Max articles fetched in full per run (0 disables)
  timeout: 10
  max_bytes: 2097152           # Max article page size in bytes (2 MB)

security:
  allow_private_networks: false  # Keep false to block SSRF to internal addresses
  csrf_origin_check: true        # Reject cross-site POSTs (Origin/Referer check)
  session_cookie_secure: false   # Set true when serving over HTTPS
  max_content_bytes: 8388608     # Reject request bodies larger than this (413)

logging:
  level: "INFO"
  file: "rss_aggregator.log"

notifications:
  email:
    enabled: false             # Enable email notifications
    smtp_server: "smtp.example.com"
    smtp_port: 587
    use_tls: true
    username: "user@example.com"
    password: "password"
    from_addr: "user@example.com"
    to_addr: "user@example.com"  # Multiple recipients: comma separated
```

---

## 🧩 Usage

### Adding an RSS Source

On the home page, fill in the form:
- **Name**: A friendly name for the source.
- **URL**: The RSS or Atom feed URL.
- **Category**: Optional category, e.g., "News", "Tech".
- **Update Interval**: Minutes between automatic fetches (minimum 5).

Click submit; the source will be fetched immediately and then updated according to the interval.

### Searching and Filtering

Use the search bar at the top of the page:
- Enter keywords to search across title, summary, and full text.
- Optionally filter by a specific source.
- Check "Unread only" to see only unread articles.

### OPML Import/Export

- **Export**: Click "Export OPML" to download an XML file containing all your sources.
- **Import**: Click "Import OPML" and choose an OPML file; sources will be added automatically, preserving categories.

### Email Notifications

To receive email alerts when new articles are fetched:
1. Set `notifications.email.enabled` to `true` in `config.yaml`.
2. Fill in your SMTP server details and recipient addresses.
3. When a source is fetched (manually or scheduled) and new articles are found, an email will be sent.

---

## 🐳 Docker Deployment

### Using Docker Compose

```bash
docker compose up -d --build
```

This will build the image and start the container in detached mode. The application will be available at `http://localhost:5000`.

### Data Persistence

The SQLite database is stored in the `./data` volume, and the configuration file is mounted read-only. To customize settings, edit `config.yaml` before starting.

### Custom Configuration

Override the default compose file if needed:

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

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

Issues and pull requests are welcome. Please ensure code style consistency and test locally.

---

## 📞 Contact

- GitHub Issues: https://github.com/forestwolf-ai/RSS-Aggregator/issues
