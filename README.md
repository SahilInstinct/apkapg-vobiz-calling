# ApkaPG Vobiz Outbound Calling & Lead Collection System

A lightweight, standalone **Python 3.12 + FastAPI** service that places outbound phone calls through **Vobiz**, either speaks a **dynamic per-call TTS script** (`<Speak>`) or plays a **pre-recorded audio file** (`intro.mp3`), captures whether the recipient **picked up** (`picked_up`) and **what they responded** (`user_input` speech text or `.mp3` recording), and stores leads locally in **JSON** and **CSV**.

> **Scope:** This service only handles outbound calling, response capture, and local JSON/CSV exports. It does not connect to any production SQL database or PG registration system.

---

## 1. Project Structure

    apkapg-vobiz-calling/
    ├── app/
    │   ├── main.py                 # FastAPI app, /health, /api/system/status
    │   ├── config.py               # Environment settings (.env)
    │   ├── api/
    │   │   ├── auth.py             # X-API-Key & service dependencies
    │   │   ├── batches.py          # Sequential batch API (/api/batches*)
    │   │   ├── calls.py            # Single call API (/api/calls*)
    │   │   ├── leads.py            # Lead listing & JSON/CSV export (/api/leads*)
    │   │   ├── ui.py               # Localhost dashboard & /ui/* proxy routes
    │   │   └── vobiz.py            # VobizXML endpoints (/api/vobiz/*) & GET/HEAD audio
    │   ├── models/
    │   │   ├── batch.py            # Batch queue & item schemas
    │   │   ├── call.py             # Call lifecycle & custom script schemas
    │   │   └── lead.py             # Minimal lead schema (picked_up + user_input)
    │   ├── services/
    │   │   ├── batch_service.py    # CSV/list parser & one-by-one batch runner
    │   │   ├── calling_service.py  # Call orchestration & lead persistence
    │   │   ├── export_service.py   # Lead filtering & JSON/CSV formatting
    │   │   └── vobiz_service.py    # Vobiz REST client, XML builder, recording downloader
    │   ├── static/
    │   │   └── index.html          # Built-in operator dashboard UI
    │   ├── storage/
    │   │   ├── csv_storage.py      # Atomic CSV writer (consistent columns)
    │   │   ├── file_lock.py        # Thread + sidecar file locking
    │   │   └── json_storage.py     # Atomic JSON repositories
    │   └── utils/
    │       ├── logger.py           # Logging with automatic secret redaction
    │       └── validators.py       # E.164 / Indian phone number normalization
    ├── scripts/
    │   └── start_tunnel.ps1        # Starts cloudflared, waits for DNS, updates .env
    ├── data/
    │   ├── audio/                  # Put intro.mp3 (and optional thank_you.mp3) here
    │   ├── batches/batches.json    # Sequential batch progress
    │   ├── calls/call_history.json # Call lifecycle & webhook events
    │   ├── leads/                  # leads.json and leads.csv
    │   └── recordings/             # Downloaded caller .mp3 files (record_audio mode)
    ├── tests/
    │   └── test_app.py             # Automated test suite
    ├── Dockerfile
    ├── docker-compose.yml
    ├── start.bat                   # One-click Windows launcher (tunnel + server + UI)
    ├── stop.bat                    # One-click shutdown (server + tunnel)
    ├── requirements.txt
    └── .env.example

---

## 2. Lead Data Structure (`data/leads/leads.json` & `leads.csv`)

    {
      "call_id": "CALL_0001",
      "campaign_id": null,
      "phone_number": "+919876543210",
      "owner_name": "Ramesh",
      "pg_name": "Sunrise PG",
      "picked_up": true,
      "user_input": "Yes I am the PG owner",
      "recording_file": null,
      "recording_url": null,
      "call_status": "completed",
      "call_duration_seconds": 18,
      "created_at": "2026-11-26T10:00:00Z",
      "updated_at": "2026-11-26T10:00:18Z"
    }

- `picked_up`: `true` if the call was answered (or completed with `duration > 0`), `false` if unanswered/busy/failed.
- `user_input`: Transcribed caller speech (or DTMF digits) in `gather_speech` mode.
- `recording_file` / `recording_url`: Local `.mp3` path under `data/recordings/` in `record_audio` mode.

---

## 3. Prerequisites & Vobiz Setup

1. **Python 3.12** (and optionally **Docker Desktop** + **`cloudflared`** for local Windows runs).
2. **Vobiz Account (`console.vobiz.ai`):**
   - Copy **Auth ID** (`MA_...`) and **Auth Token** from Dashboard → API Credentials.
   - Copy your active/trial **Phone Number** (`+91...`) from Phone Numbers.
   - No manual XML Application setup is needed for outbound calls (FastAPI passes `answer_url`, `hangup_url`, and `ring_url` per call).

---

## 4. Configuration (`.env`) & Switching Audio vs. Script

Copy `.env.example` to `.env` (never wrap values in quotes or add inline `#` comments when using Docker):

    APP_ENV=development
    PORT=8001
    API_AUTH_KEY=your-strong-api-key
    MOCK_PROVIDERS=true
    PUBLIC_BASE_URL=
    VOBIZ_AUTH_ID=MA_your_auth_id
    VOBIZ_AUTH_TOKEN=your_auth_token
    VOBIZ_CALLER_ID=+91XXXXXXXXXX
    VOBIZ_WEBHOOK_SECRET=your-webhook-secret

    VOBIZ_CAPTURE_MODE=gather_speech
    VOBIZ_SPEECH_LANGUAGE=en-IN
    VOBIZ_SPEAK_VOICE=WOMAN
    VOBIZ_NO_INPUT_TIMEOUT=3
    VOBIZ_SPEECH_END_TIMEOUT=2
    VOBIZ_MAX_RECORD_SECONDS=30

    VOBIZ_INTRO_TEXT=AUTO
    VOBIZ_THANKYOU_TEXT=Thank you for your time. Have a great day.
    VOBIZ_INTRO_AUDIO_URL=
    VOBIZ_THANKYOU_AUDIO_URL=

### Prompt Priority (TTS Script vs. Presaved MP3)
1. **Per-call / CSV row `script`** (highest priority): Supply `script` in the UI or CSV (`phone_number,owner_name,pg_name,script`). Supports `{owner_name}`, `{pg_name}`, `{notes}`, and `{phone_number}`.
2. **Global TTS script (`.env` `VOBIZ_INTRO_TEXT`):** Set to `AUTO` (auto-adapts greeting for `{owner_name}` and `{pg_name}`) or your own template.
3. **Presaved MP3 audio:** Leave `VOBIZ_INTRO_TEXT=` and `VOBIZ_THANKYOU_TEXT=` blank and place a valid MP3 (CBR 64 kbps recommended) at `data/audio/intro.mp3` (and optional `data/audio/thank_you.mp3`).

### Capture Modes (`VOBIZ_CAPTURE_MODE`)
- `gather_speech` *(default)*: Captures caller speech as text in `user_input`. Note: Vobiz `<Gather>` enforces a `5s` minimum for `executionTimeout` (`VOBIZ_NO_INPUT_TIMEOUT` values below 5 clamp to `5` here) and `2s` minimum for `VOBIZ_SPEECH_END_TIMEOUT`.
- `record_audio`: Records caller voice via `<Record>` (stops after `VOBIZ_NO_INPUT_TIMEOUT` seconds of silence, e.g., `3`s, even if the caller hangs up mid-sentence) and saves the `.mp3` under `data/recordings/`.

---

## 5. Running Locally (Windows)

### Option A: One-Click Startup (`start.bat`) — Recommended for Local Live Calls
1. Install `cloudflared` (`winget install Cloudflare.cloudflared`).
2. Double-click `start.bat` — starts Cloudflare tunnel, waits for DNS, updates `PUBLIC_BASE_URL` in `.env`, starts Docker (or `.venv` Uvicorn fallback), and opens `http://127.0.0.1:8001/`.
3. Double-click `stop.bat` when finished to stop both the backend and the tunnel.

### Option B: Manual Virtual Environment & Tests

    py -3.12 -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env
    pytest -v
    uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1

*(Always use `--workers 1` so the in-memory sequential batch runner keeps consistent state.)*

---

## 6. Using the Dashboard (`http://127.0.0.1:8001/`)

- **Initiate Single Call:** Dial one number with optional Owner Name, PG Name, Campaign ID, and Custom Script.
- **Automated Batch Calling:** Upload a `.csv` file (`phone_number,owner_name,pg_name,script`) or paste numbers one per line. Calls dial **one by one** with **Pause**, **Resume**, and **Stop** controls.
- **Collected Leads:** Filter by `Picked Up` status, view full record JSON, and download **JSON** or **CSV**.
- **Security:** Dashboard routes (`/ui/*`) only allow `localhost` (or local Docker bridge in non-production) and never expose `API_AUTH_KEY` in browser code.

---

## 7. API Reference

Interactive Swagger docs: `http://127.0.0.1:8001/docs`

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | Localhost UI | Built-in operator dashboard |
| `GET` | `/health` | None | Service & storage readiness check |
| `GET` | `/api/system/status` | None | Vobiz readiness, prompt type & capture mode |
| `POST` | `/api/calls/initiate` | `X-API-Key` | Initiate a single outbound call |
| `GET` | `/api/calls` | None | List all calls |
| `GET` | `/api/calls/{call_id}` | None | Get call details & event log |
| `POST` | `/api/batches` | `X-API-Key` | Upload CSV/list & start sequential batch |
| `GET` | `/api/batches` | None | List batches |
| `GET` | `/api/batches/{batch_id}` | None | Get batch status & per-number progress |
| `POST` | `/api/batches/{batch_id}/pause` | `X-API-Key` | Pause a running batch |
| `POST` | `/api/batches/{batch_id}/resume` | `X-API-Key` | Resume a paused batch |
| `POST` | `/api/batches/{batch_id}/stop` | `X-API-Key` | Stop batch & cancel pending numbers |
| `GET` | `/api/leads` | None | List leads (filters: `picked_up`, `campaign_id`) |
| `GET` | `/api/leads/{call_id}` | None | Get a single lead |
| `GET` | `/api/leads/export/json` | None | Download leads as JSON |
| `GET` | `/api/leads/export/csv` | None | Download leads as CSV |
| `GET/POST/HEAD` | `/api/vobiz/*` | `?token=` secret | Vobiz callbacks (`answer`, `gather`, `recording-ready`, `hangup`, `ring`, `audio`) |

---

## 8. Azure VM Production Deployment (No Tunnels Needed)

1. **Provision VM:** Create an **Ubuntu 24.04 LTS** VM (`Standard_B1ms`, 2 GB RAM) in **Central India** or **South India** with a **Static Public IP**.
2. **NSG Firewall:** Allow inbound `22` (SSH from your IP only), `80` (HTTP), and `443` (HTTPS). Keep port `8001` closed publicly.
3. **DNS:** Point an `A` record (e.g., `calling.yourdomain.com`) to the VM's Static Public IP.
4. **Install & Configure on VM:** Install `python3.12-venv` and `caddy`. Place code at `/opt/apkapg-vobiz-calling`, copy audio if used, and set `.env` (`APP_ENV=production`, `MOCK_PROVIDERS=false`, `PUBLIC_BASE_URL=https://calling.yourdomain.com`).
5. **Caddy (`/etc/caddy/Caddyfile`) — Expose Only Vobiz & Health Publicly:**

    calling.yourdomain.com {
        handle /api/vobiz/* {
            reverse_proxy 127.0.0.1:8001
        }
        handle /health {
            reverse_proxy 127.0.0.1:8001
        }
        respond "Forbidden" 403
    }

6. **Run via `systemd`:** Start `/opt/apkapg-vobiz-calling/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1` as an enabled service.
7. **Access Dashboard Securely:** Open an SSH tunnel (`ssh -L 8001:127.0.0.1:8001 azureuser@<VM_IP>`) and visit `http://127.0.0.1:8001/`. Pull leads into your main ApkaPG Azure app over private Azure VNet via `/api/leads/export/json` or `/csv`.

---

## 9. Troubleshooting

- **`400 answer_url parameter is not valid`:** `PUBLIC_BASE_URL` is blank, missing `https://`, wrapped in quotes, or points to an expired `trycloudflare.com` tunnel. Re-run `start.bat` or check `curl -i <PUBLIC_BASE_URL>/health`.
- **Call disconnects immediately on pickup (`intro.mp3`):** Confirm `data/audio/intro.mp3` is a real non-empty MP3 (not a renamed `.m4a`/`.ogg` and not 0 bytes) and that `curl -I <PUBLIC_BASE_URL>/api/vobiz/audio/intro.mp3` returns `200 OK`.
- **`user_input: null` after pickup:** Either the caller hung up before `<Gather>` finished or trial speech ASR did not fire; switch `VOBIZ_CAPTURE_MODE=record_audio` in `.env` to save the caller's `.mp3` voice clip under `data/recordings/`.
- **`403 Forbidden` on `/ui/*`:** Dashboard action routes reject non-localhost requests by design; access via `http://127.0.0.1:8001/` (or SSH tunnel on Azure).
