# LLMBrief.local

Local-only telemetry dashboard for a `llama.cpp` server and NVIDIA GPU stats.

![LLMBrief.local dashboard](docs/screenshot.png)

## Features

- Polls `llama.cpp` Prometheus metrics from `http://localhost:6688/metrics`
- Reads live slot state from `http://localhost:6688/slots`
- Reads NVIDIA telemetry with `nvidia-smi`
- Streams dashboard updates with Server-Sent Events
- Shows generation speed, prompt speed, estimated last context usage, request/queue state, GPU usage, temperature, power, clocks, and VRAM
- Runs on `127.0.0.1` only by default

## Requirements

- Windows
- Node.js 20+
- Python 3.11+
- NVIDIA driver with `nvidia-smi` available on `PATH`
- `llama.cpp` server running with `--metrics`

Example `llama-server` requirement:

```powershell
.\llama-server.exe --host 127.0.0.1 --port 6688 --metrics ...
```

## Quick Start

From PowerShell:

```powershell
.\scripts\run-local.ps1
```

Then open:

```text
http://127.0.0.1:5173
```

The script installs missing Node/Python dependencies, sets local defaults, and starts both the backend and frontend.

## Manual Setup

```powershell
pip install -r requirements.txt
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## Configuration

PowerShell script parameters:

```powershell
.\scripts\run-local.ps1 `
  -BackendPort 7171 `
  -FrontendPort 5173 `
  -LlamaMetricsUrl "http://localhost:6688/metrics"
```

Environment variables:

| Variable | Default |
| --- | --- |
| `BACKEND_PORT` | `7171` |
| `FRONTEND_PORT` | `5173` |
| `LLAMA_METRICS_URL` | `http://localhost:6688/metrics` |
| `POLL_INTERVAL_SECONDS` | `0.1` |
| `GPU_POLL_INTERVAL_SECONDS` | `1` |
| `MODEL_NAME` | optional override; otherwise read from `llama.cpp /props` |

## API

- `GET /api/status` returns the current normalized telemetry snapshot.
- `GET /api/events` streams telemetry snapshots through SSE.

## Notes

`Last context usage est.` is an estimate. `llama.cpp` exposes slot capacity through `/slots.n_ctx`, but the current KV/context occupancy is not directly exposed by `/metrics` in the tested build. The dashboard estimates last context usage from prompt token deltas plus decoded slot tokens.

If `llama.cpp` or `nvidia-smi` is unavailable, the dashboard stays up and shows meaningful fallback/offline states.
