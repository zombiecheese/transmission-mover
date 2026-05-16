# Transmission Mover

Transmission Mover is a FastAPI + web UI service that routes completed Transmission torrents to destinations based on labels.

It is built for NAS/home-lab workflows where labels determine where data goes, how it is transferred, and whether torrents are removed from the client afterward.

## What It Does

- Polls Transmission RPC and reads torrent metadata/labels.
- Matches torrent labels to enabled rules.
- Moves or copies data according to per-rule options.
- Supports local and remote SSH watch sources.
- Supports local and remote SSH destinations.
- Auto-detects transfer capabilities (`rsync`, `scp`, `sftp`) for source/destination.
- Shows active transfer progress and method in the UI.
- Logs all transfer outcomes to SQLite-backed activity logs.

## Current Transfer Behavior

### Source/Destination Combinations

- Local source -> local destination: filesystem move/copy.
- Local source -> remote destination: tries remote methods (`rsync`/`scp`/`sftp`) based on capabilities and preferences.
- Remote SSH source -> local destination: direct SFTP download to local destination (no staging).
- Remote SSH source -> remote destination:
  1. Try direct source->destination shell transfer (`rsync`/`scp`).
  2. If that fails, try reverse destination->source pull (`rsync`/`scp`).
  3. If both fail, fall back to staged transfer via local staging volume and SFTP.

### Fallback Visibility

Fallback reasons are logged with method-level details (missing commands, auth constraints, command failures, stderr). Active transfer telemetry updates method to `staged-sftp` when staged fallback is selected.

## Rule Matching Semantics

- Torrents can have multiple labels.
- Matching uses the first torrent label that has an enabled rule.
- Only one rule is applied per processed torrent.

## Scheduling Semantics

- Worker poll cadence is controlled by `POLL_SECONDS`.
- Rules support `auto`, `interval`, and `manual` schedule modes.
- The UI button **Run All Now** triggers an immediate one-time cycle and bypasses schedule timing gates for that run.

## Security Model

- Secrets are encrypted at rest (Fernet) in SQLite.
- Web auth uses a login form and cookie session (`tm_session`), not browser Basic Auth.
- Credentials can be initialized from `WEB_AUTH_USERNAME` and `WEB_AUTH_PASSWORD` on first startup only.
- If credentials are not configured, API/UI are open until auth is set up.
- Failed login attempts are rate-limited with increasing lockout.

## API Accessibility

The API is not UI-only. External clients (scripts, Postman, automation) can consume it if they can reach the service and authenticate.

## Quick Start (Docker Compose)

```bash
docker compose up -d --build
```

Open:

http://localhost:8080

Stop:

```bash
docker compose down
```

## Docker Compose Notes

The sample compose mounts:

- `./data:/data` for SQLite DB and generated encryption key file.
- `./static:/app/static` for live-editable UI assets.
- `./watch:/watch` as an example local path mount.
- `/path/to/staging/volume:/staging` for remote-to-remote staging fallback.

Replace `/path/to/staging/volume` with a real writable path on the Docker host.

## Environment Variables

- `APP_NAME` (default: `Transmission Mover`)
- `POLL_SECONDS` (default: `20`)
- `DATABASE_URL` (default: `sqlite:///./data/app.db`)
- `LOG_LEVEL` (default: `INFO`)
- `SECRET_ENCRYPTION_KEY` (optional; auto-generated and persisted to `/data/.encryption_key` if missing)
- `WEB_AUTH_USERNAME` (optional; first-start auth bootstrap only)
- `WEB_AUTH_PASSWORD` (optional; first-start auth bootstrap only)
- `STAGING_PATH` (default: `/staging`)

## Initial Configuration Flow (UI)

1. Configure Transmission RPC and test connection.
2. Configure source under **Source**:
   - local path or remote SSH watch source
   - optional path remapping if Transmission path differs from container-visible path
3. Add destinations and run destination test.
4. Create label rules (destination, mode, schedule, transfer preference, remove/trash behavior).
5. Use **Run All Now** for an immediate validation run.

## Destination Validation Guarantees

Before save/test succeeds, destination validation checks:

- Local destination exists, is a directory, and writable.
- Remote destination path exists and is a directory.
- Remote destination allows file write probes.
- Remote destination allows subdirectory create/remove probes.
- Remote transfer method capabilities are detected and stored.

## API Endpoints (Current)

### Health

- `GET /api/health`

### Authentication

- `POST /api/auth/setup`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/change-password`

### Transmission Config and Tests

- `GET /api/transmission`
- `PUT /api/transmission`
- `POST /api/transmission/test`
- `POST /api/transmission/torrents`
- `POST /api/transmission/torrents/label`
- `POST /api/transmission/torrents/label/remove`

### App Settings

- `GET /api/app-settings`
- `PUT /api/app-settings`
- `PUT /api/app-settings/transmission-container`
- `POST /api/sftp/test`

### Destinations

- `GET /api/destinations`
- `POST /api/destinations`
- `PUT /api/destinations/{destination_id}`
- `DELETE /api/destinations/{destination_id}`

### Rules

- `GET /api/rules`
- `POST /api/rules`
- `PUT /api/rules/{rule_id}`
- `DELETE /api/rules/{rule_id}`

### Activity and Execution

- `POST /api/run-once`
- `POST /api/transfer/torrent/{torrent_id}`
- `GET /api/transfers/active`
- `GET /api/logs?limit=100`

## Logging and Observability

- Active transfer telemetry exposes:
  - torrent id/name
  - destination
  - mode (`move`/`copy`)
  - method (for example `rsync`, `scp`, `sftp`, `staged-sftp`)
  - bytes transferred, speed, percent
- Persistent transfer logs capture final outcome and message.
- Remote-to-remote fallback logging now includes concrete reasons for direct and reverse failure before staging fallback.

## Static UI Customization

UI assets are mounted from `./static` to `/app/static` and can be edited live:

- `static/index.html`
- `static/app.js`
- `static/styles.css`

## Operational Notes

- Keep this service on trusted networks or behind a reverse proxy.
- If exposing over HTTPS via proxy, set cookie security/proxy headers appropriately.
- SQLite is suitable for single-instance deployments. For high write concurrency, expect occasional lock pressure and tune deployment accordingly.

## Troubleshooting

### “No enabled rules with valid destinations”

- No enabled rule currently maps to an existing destination.

### “No rules are due to run at this time”

- Rules exist, but schedule windows are not currently due.

### Remote transfer permission failures

- Destination user may not have create/write permissions under the configured base path.
- Re-run destination test; directory create probe failures should be reported before save.

### Path mismatch issues (containerized Transmission)

- Enable path remapping and configure source/target prefixes to map Transmission-reported paths to container-visible paths.
