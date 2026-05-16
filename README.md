# Transmission Mover

Transmission Mover is a FastAPI service with a built-in web UI that routes completed Transmission torrents to one or more destinations based on torrent labels. It is designed for NAS and home-lab workflows where labels determine *where* completed data lands, *how* it is moved, and *what* happens to the torrent in the client afterward.

---

## Capabilities

### Core function

- Polls the Transmission RPC endpoint and reads torrent metadata and labels.
- Matches each torrent against enabled label rules.
- Moves or copies completed torrent data to the rule's destination.
- Optionally removes the torrent from Transmission after a successful transfer, with optional data trash.
- Records every transfer outcome and active progress in a SQLite-backed activity log.

### Source modes

- **Local source** — completed data is read from a filesystem path visible to this container/host.
- **Remote SSH source** — completed data is read from a remote host via SFTP, then transferred directly to a local destination.

### Destination modes

- **Local destination** — filesystem path mounted into this container/host.
- **Remote SSH destination** — transferred to a remote host using the negotiated method (`rsync`, `scp`, or `sftp`).

### Topology rule

Remote-to-remote transfers are **not supported**. At least one side of every rule must be local. The UI and API enforce this constraint.

### Transfer method negotiation

For remote endpoints, the app probes the remote host during the **Test Connection** step and stores the detected capabilities:

- Available methods (`rsync`, `scp`, `sftp`)
- Preferred method
- Detected ports for SFTP / SCP / rsync

Each destination may pin a preferred transfer method or leave it on `auto`.

### Authentication

- Cookie-based session login (`tm_session`), not browser Basic Auth.
- First-startup bootstrap from `WEB_AUTH_USERNAME` / `WEB_AUTH_PASSWORD` environment variables.
- Subsequent credential changes via `POST /api/auth/setup` or `POST /api/auth/change-password`.
- Failed-login rate limiting with progressive lockout.

### Secrets handling

- All SSH passwords, private keys, and passphrases are encrypted at rest with Fernet.
- Encryption key is provided via `SECRET_ENCRYPTION_KEY` or auto-generated and persisted to `/data/.encryption_key` on first launch.

### Path remapping

When Transmission runs in a separate container and reports paths that this app cannot see directly, you can configure a prefix remap:

- Source prefix (as Transmission reports)
- Target prefix (as visible inside this container)

---

## Operation

### Initial setup flow (UI)

1. **Transmission** — enter RPC domain, optional port, optional path (default `/transmission/rpc`), credentials, and TLS verify. Run **Test Connection**.
2. **Source** — choose `local` or `remote SSH`. For remote SSH, provide host, port, credentials, and base path; then **Test Connection** to validate and negotiate methods.
3. **Destinations** — add one or more destinations (local or remote SSH). Each remote destination must pass **Test Connection** before save.
4. **Rules** — bind a label to a destination, choose transfer mode (`move` / `copy`), schedule (`auto` / `interval` / `manual`), preferred transfer method, removal behavior, and trash behavior.
5. **Run All Now** (optional) — immediate one-shot cycle that bypasses schedule timing for that run.

### Rule matching semantics

- Torrents may carry multiple labels.
- The first torrent label that matches an enabled rule wins.
- Only one rule is applied per torrent per cycle.

### Scheduling semantics

- The background worker runs every `POLL_SECONDS`.
- `auto` — runs as soon as the worker is due and the torrent is complete.
- `interval` — minimum gap between attempts per torrent, controlled by `transfer_interval_seconds`.
- `manual` — never runs automatically; only via **Run All Now** or per-torrent **Transfer Now**.

### Destination validation guarantees

Saving a remote destination requires a fresh successful test that proves:

- Path exists and is a directory.
- Account can list the directory.
- Account can write a probe file.
- Account can create and remove a subdirectory.
- Capability detection (rsync/scp/sftp/ports) succeeded.

If any of these fail, the test surfaces the specific failure and save is blocked until corrected.

### Activity and logs

- `GET /api/transfers/active` reports current transfers with torrent id/name, destination, mode, method, bytes, speed, percent.
- `GET /api/logs` returns persisted outcomes with status and message.

---

## API surface

### Health
- `GET /api/health`

### Auth
- `POST /api/auth/setup`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/change-password`

### Transmission
- `GET /api/transmission`
- `PUT /api/transmission`
- `POST /api/transmission/test`
- `POST /api/transmission/torrents`
- `POST /api/transmission/torrents/label`
- `POST /api/transmission/torrents/label/remove`

### App settings
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

### Activity
- `POST /api/run-once`
- `POST /api/transfer/torrent/{torrent_id}`
- `GET /api/transfers/active`
- `GET /api/logs?limit=100`

The API is consumable by any external client that can reach the service and authenticate.

---

## Deployment

### Quick start (Docker Compose)

```bash
docker compose up -d --build
```

Open: <http://localhost:8080>

Stop:

```bash
docker compose down
```

### Container layout

The provided `docker-compose.yml` mounts:

| Host path  | Container path | Purpose |
|------------|----------------|---------|
| `./data`   | `/data`        | SQLite database and persistent encryption key. |
| `./static` | `/app/static`  | Web UI assets (live-editable). |
| `./watch`  | `/watch`       | Example mount for a local source/destination path. |

Bind any additional host paths that need to be visible as local sources or local destinations.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `Transmission Mover` | Display name. |
| `POLL_SECONDS` | `20` | Worker cadence. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy URL. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `SECRET_ENCRYPTION_KEY` | *(auto)* | Fernet key. Auto-generated to `/data/.encryption_key` if unset. |
| `WEB_AUTH_USERNAME` | *(unset)* | First-startup bootstrap username. |
| `WEB_AUTH_PASSWORD` | *(unset)* | First-startup bootstrap password. |

If no credentials exist in the database and no bootstrap env vars are set, the API/UI is open until credentials are configured. Always set credentials before exposing the service.

### Running outside Docker

Requirements:

- Python 3.12+
- `openssh-client` available (rsync/scp need their respective binaries in PATH for those methods to be selectable).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Reverse proxy / TLS

- Keep the service on trusted networks or behind a reverse proxy.
- When fronted by HTTPS, ensure the proxy forwards standard `X-Forwarded-*` headers so session cookies behave correctly.

### Database notes

- SQLite is suitable for single-instance deployments.
- PRAGMAs enable WAL and a long busy-timeout to handle concurrent worker/API access.

---

## UI customization

UI assets are served from `/app/static` and bind-mounted from `./static`. Edits to the following take effect on browser reload:

- `static/index.html`
- `static/app.js`, `static/actions.js`, `static/render.js`, `static/shared.js`, `static/state.js`, `static/utils.js`
- `static/styles.css`

---

## Troubleshooting

### "No enabled rules with valid destinations"
No enabled rule currently maps to an existing destination.

### "No rules are due to run at this time"
Rules exist but no schedule window is currently due. Use **Run All Now** to bypass scheduling for a one-shot cycle.

### Remote transfer permission failures
Re-run the destination **Test Connection**. The validator reports the specific probe (list / write / mkdir / traverse) that failed and the remote error message. Adjust remote ownership/permissions or the configured account accordingly.

### Path mismatch (containerized Transmission)
Enable **Remap Transmission download paths** and configure source/target prefixes so paths reported by Transmission resolve to paths visible inside this container.

### Cannot save destination
Destination save requires a fresh successful **Test Connection** that matches the form's current host/credentials/path signature. Any change to those fields invalidates the test and you must retest before saving.

### Lost encryption key
If `/data/.encryption_key` is deleted and `SECRET_ENCRYPTION_KEY` is not set, previously stored SSH secrets cannot be decrypted. Re-enter credentials for each SSH source/destination.
