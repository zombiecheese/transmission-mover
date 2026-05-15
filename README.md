# Transmission Mover

Transmission Mover is a containerized FastAPI web application that watches completed Transmission torrents and transfers payloads based on label rules.

It is designed for home lab and NAS workflows where you want automatic post-download routing (for example: movies to one path, TV to another, or to a remote SFTP host).

## Core Capabilities

- Polls Transmission RPC on a schedule.
- Reads torrent labels and matches them to enabled routing rules.
- Supports per-rule transfer mode: move or copy.
- Supports per-rule scheduling: auto, interval, or manual.
- Supports local and remote watch sources:
  - local source from mounted paths
  - SFTP source from a remote host
- Supports local and remote destinations:
  - local filesystem destination
  - remote destination over SFTP (with optional rsync/scp auto-selection when available)
- Optional remove-from-Transmission after successful transfer.
- Manual actions from UI/API:
  - run one full scan now
  - transfer a specific torrent now
  - assign labels to torrents
- Transfer visibility:
  - active transfer progress endpoint
  - persistent transfer logs in SQLite
- Web UI is served by the same container and can be live-customized through a mounted static directory.

## How It Works

1. Configure Transmission RPC connection in the web UI.
2. Configure app-level behavior (watch source, transfer defaults, optional path remap).
3. Create destinations.
4. Create label rules that map label -> destination.
5. The worker runs every POLL_SECONDS and checks completed torrents.
6. When a completed torrent has a matching enabled label rule, data is transferred and logged.

## Requirements

- A Transmission instance with RPC enabled.
- Transmission label support (the app relies on the labels field from RPC).
- Correct volume mounts for any local source/destination paths.

## Quick Start (Docker Compose)

From the project root:

```bash
docker compose up -d --build
```

Open the UI:

http://localhost:8080

Stop:

```bash
docker compose down
```

The included compose file maps:

- ./data -> /data (SQLite database)
- ./static -> /app/static (UI assets)
- ./watch -> /watch (example local watch/destination mount)

## Deploy with Docker CLI (Example)

Build image:

```bash
docker build -t transmission-mover:latest .
```

Run container with equivalent defaults:

```bash
docker run -d \
  --name transmission-mover \
  -p 8080:8080 \
  -e POLL_SECONDS=20 \
  -e DATABASE_URL=sqlite:////data/app.db \
  -e LOG_LEVEL=INFO \
  -v "${PWD}/data:/data" \
  -v "${PWD}/static:/app/static" \
  -v "${PWD}/watch:/watch" \
  --restart unless-stopped \
  transmission-mover:latest
```

Windows PowerShell variant:

```powershell
docker run -d `
  --name transmission-mover `
  -p 8080:8080 `
  -e POLL_SECONDS=20 `
  -e DATABASE_URL=sqlite:////data/app.db `
  -e LOG_LEVEL=INFO `
  -v "${PWD}/data:/data" `
  -v "${PWD}/static:/app/static" `
  -v "${PWD}/watch:/watch" `
  --restart unless-stopped `
  transmission-mover:latest
```

Enable Basic Auth (optional) by setting both variables on first startup:

```bash
docker run -d \
  --name transmission-mover \
  -p 8080:8080 \
  -e WEB_AUTH_USERNAME=admin \
  -e WEB_AUTH_PASSWORD=change-me \
  -v "${PWD}/data:/data" \
  -v "${PWD}/static:/app/static" \
  -v "${PWD}/watch:/watch" \
  --restart unless-stopped \
  transmission-mover:latest
```

Note: WEB_AUTH_USERNAME and WEB_AUTH_PASSWORD are only used on first startup to initialize credentials in the database. After initialization, they are ignored on subsequent restarts.
```

For Docker Compose, set these in the service environment:

```yaml
- WEB_AUTH_USERNAME=admin
- WEB_AUTH_PASSWORD=change-me
```

When enabled, HTTP Basic authentication protects both the web UI and API endpoints.

## Configuration Flow After Deployment

1. Open UI and set Transmission RPC under Transmission settings.
2. Test connection.
3. Set app settings:
  - transfer mode and schedule defaults
  - remove torrent on complete (optional)
  - watch source type (local or sftp)
  - optional download path remap
4. Add destinations.
5. Add label rules.
6. Trigger Run Once (optional) to validate end-to-end transfer.

## Volume and Path Mapping Notes

For local source and local destination operations, the mover container must be able to see those paths.

If Transmission reports a downloadDir path not valid inside the mover container, use app settings path remap:

- remap_download_path = true
- remap_source_prefix = path reported by Transmission
- remap_target_prefix = equivalent mounted path inside mover container

If watch_source_kind is local and watch_base_path is set, the app uses watch_base_path/torrent_name.

If watch_source_kind is local and watch_base_path is empty, the app uses Transmission downloadDir/torrent_name.

If watch_source_kind is sftp, the app reads from remote SFTP source and then transfers onward per rule.

## Web UI Static Files

The static folder is mounted from host to container at ./static -> /app/static.

On first start, if /app/static is empty, entrypoint seeds default UI files.
Existing files are not overwritten.

Editable files:

- static/index.html
- static/app.js
- static/styles.css

## Environment Variables

Defined in .env.example:

- APP_NAME (default: Transmission Mover)
- POLL_SECONDS (default: 20)
- DATABASE_URL (default: sqlite:///./data/app.db)
- LOG_LEVEL (default: INFO)
- SECRET_ENCRYPTION_KEY (auto-generated on first startup if not provided; Fernet key for encrypted secret storage)
- WEB_AUTH_USERNAME (optional; used only on first startup to initialize database credentials)
- WEB_AUTH_PASSWORD (optional; used only on first startup to initialize database credentials)

## API Overview

- Health:
  - GET /api/health
- Transmission:
  - GET /api/transmission
  - PUT /api/transmission
  - POST /api/transmission/test
  - POST /api/transmission/torrents
  - POST /api/transmission/torrents/label
- App settings:
  - GET /api/app-settings
  - PUT /api/app-settings
- Destinations:
  - GET /api/destinations
  - POST /api/destinations
  - PUT /api/destinations/{destination_id}
  - DELETE /api/destinations/{destination_id}
- Rules:
  - GET /api/rules
  - POST /api/rules
  - PUT /api/rules/{rule_id}
  - DELETE /api/rules/{rule_id}
- Manual transfer actions:
  - POST /api/run-once
  - POST /api/transfer/torrent/{torrent_id}
- Transfer visibility:
  - GET /api/transfers/active
  - GET /api/logs?limit=100

## Security Notes

- Secrets and credentials are encrypted at rest in SQLite using Fernet.
- You must keep SECRET_ENCRYPTION_KEY stable across restarts to decrypt stored values.
- If SECRET_ENCRYPTION_KEY is not provided, one is auto-generated and persisted to `/data/.encryption_key`.
- HTTP Basic auth is optionally available:
  - **Initial Setup**: Set WEB_AUTH_USERNAME and WEB_AUTH_PASSWORD environment variables on first startup.
  - **Storage**: Credentials are hashed using bcrypt and stored in the database, not in environment variables.
  - **Setup Endpoint**: If no credentials exist, call POST /api/auth/setup with `{"username": "...", "password": "..."}`.
  - **Rate Limiting**: Failed auth attempts are rate-limited (5 attempts per 5 minutes per IP).
  - **Audit Logging**: All authentication attempts are logged to the audit trail in the database.
- Keep this service behind a trusted LAN, VPN, or reverse proxy authentication.
- Do not expose it directly to the public internet without additional hardening.

## Secret Key Rotation

You can rotate encrypted secret fields to a new Fernet key with a one-time CLI command.

1. Stop the app.
2. Run rotation with old and new keys.
3. Update SECRET_ENCRYPTION_KEY to the new key.
4. Start the app.

Example:

```bash
python -m app.rotate_secrets \
  --old-key "<old_fernet_key>" \
  --new-key "<new_fernet_key>" \
  --database-url "sqlite:////data/app.db"
```

## Troubleshooting Quick Checks

- No torrents processed:
  - verify label exists on completed torrent
  - verify rule is enabled and destination exists
  - verify transfer schedule is due
- Local path errors:
  - verify host-to-container volume mount
  - verify downloadDir path remap if Transmission path differs
- Remote transfer fallback behavior:
  - app can use sftp directly
  - rsync/scp are attempted when tools and remote capabilities are available
