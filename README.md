# Transmission Mover

A FastAPI service with a built-in web UI that routes completed Transmission
torrents to one or more destinations based on their labels.

Pick a label → pick a destination → done. Local or remote (SSH) on either side
(but not both), with per-rule transfer mode, conflict policy, and scheduling.

---

## Features

- **Label-driven routing.** Match torrents by label, move/copy them to the
  configured destination, optionally remove the torrent (and trash data) after.
- **Local or SSH** sources and destinations. Remote-to-remote is not supported —
  one side of each rule must be local.
- **Auto-negotiated transfer method.** Probes remote hosts for `rsync` / `scp` /
  `sftp` availability and ports during Test Connection; each destination can
  pin a preferred method or use `auto`.
- **Per-rule controls.** Transfer mode (`move`/`copy`), execution mode
  (sequential/parallel), conflict policy (overwrite/rename/skip), schedule
  (`auto`/`interval`/`manual`), removal + trash behavior.
- **Global concurrency cap** via `max_parallel_transfers`.
- **Path remapping** for Transmission running in a separate container that
  reports paths this app can't see directly.
- **Session-cookie auth** with progressive lockout on failed logins.
- **Encrypted secrets at rest** (Fernet) for all SSH passwords/keys/passphrases.
- **Activity log** of in-flight + historical transfers, backed by SQLite (WAL).

---

## Quick start

```bash
docker compose up -d --build
```

Open <http://localhost:8080>.

If `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD` are set in `docker-compose.yml`,
those credentials are seeded on first startup. Otherwise the UI is open until
you configure credentials via the Security panel.

### Compose mounts

| Host       | Container       | Purpose                                          |
|------------|-----------------|--------------------------------------------------|
| `./data`   | `/data`         | SQLite DB and the persistent Fernet key file.    |
| `./static` | `/app/static`   | Web UI assets (live-editable; see below).        |
| `./watch`  | `/watch`        | Example local source/destination mount.          |

Bind any additional host paths you want available as local sources or
destinations.

### Environment variables

| Variable                 | Default                   | Description                                                 |
|--------------------------|---------------------------|-------------------------------------------------------------|
| `POLL_SECONDS`           | `20`                      | Worker cadence.                                             |
| `DATABASE_URL`           | `sqlite:///./data/app.db` | SQLAlchemy URL.                                             |
| `LOG_LEVEL`              | `INFO`                    | Python log level.                                           |
| `SECRET_ENCRYPTION_KEY`  | *(auto)*                  | Fernet key. Auto-generated to `/data/.encryption_key`.      |
| `WEB_AUTH_USERNAME`      | *(unset)*                 | First-startup bootstrap username.                           |
| `WEB_AUTH_PASSWORD`      | *(unset)*                 | First-startup bootstrap password.                           |

---

## Setup flow (in the UI)

1. **Transmission** — RPC domain/port/path, credentials, TLS verify → Test
   Connection.
2. **Source** — `local` or `remote SSH`. For SSH, fill host/port/credentials/
   base path → Test Connection (negotiates transfer methods).
3. **Destinations** — add one or more. Remote destinations require a passing
   Test Connection before save.
4. **Label Rules** — bind a label to a destination and pick mode, schedule,
   conflict policy, etc.
5. **Run All Now** (optional) for an immediate one-shot cycle.

### Rule matching

- Torrents may carry multiple labels.
- The first label matching an enabled rule wins.
- One rule is applied per torrent per cycle.

### Scheduling

- `auto` — runs as soon as the worker is due and the torrent is complete.
- `interval` — minimum gap between attempts per torrent.
- `manual` — only via **Run All Now** or per-torrent **Transfer Now**.

### Destination test guarantees

A remote destination save requires a fresh successful test that verifies the
path exists, is a directory, is readable/writable, can mkdir + cleanup, and
that capabilities (rsync/scp/sftp/ports) were detected. Any field change
invalidates the test until you re-run it.

---

## API surface

All endpoints are under `/api`. Authentication is via the `tm_session` cookie
once credentials are configured.

**Health** — `GET /health`

**Auth** — `POST /auth/setup`, `POST /auth/login`, `POST /auth/logout`,
`POST /auth/change-password`

**Transmission** — `GET|PUT /transmission`, `POST /transmission/test`,
`POST /transmission/torrents`, `POST /transmission/torrents/label`,
`POST /transmission/torrents/label/remove`

**App settings** (split by section so each save only touches its own scope):

- `GET  /app-settings`
- `PUT  /app-settings/source`
- `PUT  /app-settings/remap`
- `PUT  /app-settings/ignored-labels`
- `PUT  /app-settings/transmission-container`
- `POST /app-settings/reseed-static` — overwrite the live `static/` dir with
  the image-baked defaults (see UI customization below).
- `POST /sftp/test`

**Destinations** — `GET|POST /destinations`, `PUT|DELETE /destinations/{id}`

**Rules** — `GET|POST /rules`, `PUT|DELETE /rules/{id}`

**Activity** — `POST /run-once`, `POST /transfer/torrent/{id}`,
`GET /transfers/active`, `GET|DELETE /logs`

---

## UI customization

Web UI assets are served from `/app/static` and bind-mounted from `./static`.
Edits to any of these take effect on browser reload:

- `static/index.html`, `static/login.html`
- `static/app.js`, `static/actions.js`, `static/render.js`, `static/shared.js`,
  `static/state.js`, `static/utils.js`
- `static/styles.css`

To undo local edits and restore the version shipped in the image, use
**Settings → Security → Reset Web Files** (or
`POST /api/app-settings/reseed-static`). The page reloads automatically.

---

## Running outside Docker

Requires Python 3.12+ and `openssh-client` on PATH. `rsync` / `scp` must also
be installed if you want those methods selectable.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The reset-web-files feature requires the image-baked defaults at
`/opt/default-static`; it returns 400 in dev environments where that directory
doesn't exist.

---

## Operational notes

- Keep the service on trusted networks or behind a reverse proxy. When fronted
  by HTTPS, forward `X-Forwarded-*` so session cookies behave correctly.
- SQLite is fine for single-instance deployments; WAL + a long busy-timeout
  handle concurrent worker/API access.
- If `/data/.encryption_key` is deleted and `SECRET_ENCRYPTION_KEY` is unset,
  stored SSH secrets become unreadable and must be re-entered.

---

## Troubleshooting

| Symptom | Most likely cause |
|---------|-------------------|
| "No enabled rules with valid destinations" | No enabled rule points at an existing destination. |
| "No rules are due to run at this time" | Schedules aren't due yet — use **Run All Now**. |
| Remote transfer permission failures | Re-run destination Test Connection; it reports the specific probe (list / write / mkdir) that failed. |
| Paths from Transmission don't exist here | Enable **Remap Transmission download paths** and set source/target prefixes. |
| Can't save a destination | Test Connection signature is stale — retest with current form values. |
