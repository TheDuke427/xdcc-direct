# XDCC Download Manager

A self-hosted web UI for searching and downloading files from XDCC bots on IRC networks. All IRC traffic routes through ProtonVPN via a gluetun sidecar.

## Features

- **Local index** — background process joins configured IRC channels and passively records every pack announcement into a SQLite database. Stale entries (7+ days unseen) are pruned automatically.
- **Search** — full-text search across the local index with multi-word AND filtering.
- **Pack list** — browse a bot's full pack list in a modal and queue individual packs from there.
- **Download queue** — concurrent XDCC downloads with progress, speed, ETA, and resume support. Live updates via WebSocket.
- **File library** — browse and download completed files from the web UI.
- **VPN routing** — all backend IRC/HTTP traffic goes through ProtonVPN WireGuard (gluetun). DNS resolves via Cloudflare DNS-over-HTTPS to work inside the VPN namespace.
- **Index management** — add or remove IRC channels from the Index tab; connection status and pack counts update in real time.

## Architecture

```
Browser
  └── frontend (nginx :3000)
        └── /api/* → backend (FastAPI :8080)
                        ├── DownloadManager   active XDCC downloads
                        ├── Indexer           passive channel listeners
                        │     └── ChannelIndexer × N   one per channel
                        └── SQLite            xdcc_index.db (in downloads volume)

All backend network traffic → gluetun (ProtonVPN WireGuard)
```

### Services

| Service | Description |
|---------|-------------|
| `gluetun` | ProtonVPN WireGuard sidecar. Backend shares its network namespace. |
| `backend` | FastAPI app. Manages downloads, runs the IRC indexer, serves the REST API. |
| `frontend` | Vite/React SPA served by nginx. Proxies `/api/*` and `/ws` to the backend. |

### Backend modules

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, all REST endpoints, WebSocket broadcast. |
| `irc_client.py` | Async IRC client: XDCC send/resume/download, pack list fetch, DoH DNS. |
| `download_manager.py` | Job queue, concurrency control, file resume, progress callbacks. |
| `indexer.py` | `ChannelIndexer` — passive listener per channel with auto-reconnect. `Indexer` — manages all indexers, starts on app boot, hourly prune. |
| `db.py` | SQLite helpers: schema init, bulk upsert, pack search, channel CRUD, stats. |

### Frontend components

| Component | Purpose |
|-----------|---------|
| `SearchTab` | Search the local index; Get button queues a download; List button opens PackListModal. |
| `PackListModal` | Fetches a live pack list from a bot; shows all bots broadcasting in the channel; queues packs. |
| `DownloadQueue` | Live job table (WebSocket-driven) with cancel/delete. |
| `AddDownloadForm` | Manual XDCC request form (server, bot, pack). |
| `FileLibrary` | Lists completed downloads with in-browser download links. |
| `IndexTab` | Shows index stats, per-channel connection status, and add/remove channel form. |

## Setup

### Prerequisites

- Docker and Docker Compose
- A ProtonVPN account with WireGuard access

### 1. Get your WireGuard private key

Log in to [protonvpn.com](https://protonvpn.com) → Downloads → WireGuard configuration → generate a config for any server → copy the `PrivateKey` value from the `[Interface]` section.

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
PROTON_WG_PRIVATE_KEY=<your key>
PROTON_SERVER_COUNTRIES=Netherlands   # or your preferred country
DOWNLOADS_PATH=/your/download/path    # host path for completed files
```

### 3. Configure the downloads volume

Edit `docker-compose.yml` and set the backend volume to your local download path:

```yaml
volumes:
  - /your/download/path:/app/downloads
```

### 4. Start

```bash
docker compose up -d
```

Open [http://localhost:3000](http://localhost:3000).

Gluetun takes ~30 seconds to establish the VPN tunnel before the backend starts. The index begins filling as soon as the backend is up and connected to the starter channels.

## Usage

### Searching

The **Search** tab queries the local SQLite index. Multi-word queries use AND logic — `x265 2160p` finds entries containing both terms. The index starts empty and fills as bots broadcast in joined channels. Add more channels in the **Index** tab to expand coverage.

### Downloading

Click **Get** on any search result to queue an immediate download. The download connects to the IRC server, joins the channel, sends `XDCC SEND #N` to the bot, and transfers the file via DCC. Progress, speed, and ETA show in the **Queue** tab.

Interrupted downloads resume automatically if the partial file is still present.

### Pack list

Click **List** on any search result to open a live pack list for that channel. The modal shows all packs announced by any bot currently active in the channel. Click **Get** on any row to queue that pack.

### Index tab

Shows:
- Total indexed packs and how many channels are connected
- Per-channel status (connected / reconnecting), packs seen this session, last activity time
- Form to add a new channel (network label, server hostname, port, SSL, channel name)

Starter channels (CoreIRC, Rizon, Abjects — all `#elitewarez`) are seeded on first run. Add any XDCC channel from any network.

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PROTON_WG_PRIVATE_KEY` | *(required)* | WireGuard private key for ProtonVPN |
| `PROTON_SERVER_COUNTRIES` | `Netherlands` | VPN exit country |
| `DOWNLOADS_PATH` | `./downloads` | Host path mounted into the backend container |
| `DOWNLOAD_DIR` | `/app/downloads` | Path inside the container (change only if you change the volume mount target) |
| `MAX_CONCURRENT` | `3` | Maximum simultaneous XDCC downloads |
| `DB_PATH` | `/app/downloads/xdcc_index.db` | SQLite database location (inside container) |

## Deployment notes

- The backend runs inside gluetun's network namespace (`network_mode: service:gluetun`). It has no direct internet access — all traffic goes through the VPN.
- DNS inside the VPN namespace uses Cloudflare DNS-over-HTTPS (`cloudflare-dns.com:443`) because gluetun overwrites Docker's internal DNS DNAT rules, making the standard resolver unavailable.
- The nginx frontend uses a runtime variable for the upstream (`set $backend gluetun:8080`) with `resolver 127.0.0.11` so it resolves the backend hostname after startup, not at config load time.
- The SQLite database is stored in the downloads volume so it persists across container rebuilds.
