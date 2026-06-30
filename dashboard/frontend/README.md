# NetScope — KNUST Network Operations Center

A real-time network anomaly detection dashboard built for the KNUST campus NOC. NetScope monitors network devices across campus buildings, detects anomalies using AI-based detectors, and presents actionable alerts to NOC engineers.

## Overview

NetScope provides a single-pane-of-glass view of campus network health. It connects to a FastAPI backend that runs AI models (bandwidth, port scan, device behaviour, protocol anomaly detectors) and surfaces their findings through an intuitive dashboard.

### Pages

| Page | What it shows |
|------|--------------|
| **Overview** | Building-level health summary with PulseStrip visualizations, device lists, and open alerts |
| **Devices** | Searchable, sortable table of all network devices with health scores |
| **Device Detail** | Per-device bandwidth/packet charts, metrics, and alert history |
| **Alerts** | Filterable alert history with severity distribution and detector breakdown |
| **Traffic** | Aggregate bandwidth charts, live detector scores, per-device traffic grid |
| **Settings** | System configuration (placeholder) |

### Key Features

- **5-level severity scale** — info, low, medium, high, critical (mapped from anomaly scores 0.0–1.0)
- **System lifecycle awareness** — adapts UI to observation, training, and inference phases
- **Live polling** — auto-refreshes data every 10 seconds
- **Toast notifications** — real-time alerts for new anomalies
- **Authentication** — login/signup with rate limiting (5 attempts, 15-min lockout), auto-logout after 30 min inactivity, and 401 session expiry handling
- **Mock data mode** — fully functional demo without a backend

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 18 + Vite 5 |
| Routing | React Router 6 |
| Styling | Raw CSS with custom properties (no frameworks) |
| Charts | Recharts |
| Icons | Lucide React |
| Fonts | Inter (UI) + IBM Plex Mono (data) |
| State | React Context + useReducer |
| HTTP | Native fetch API |

## Getting Started

### Prerequisites

- Node.js 18+
- npm

### Installation

```bash
git clone https://github.com/Kinpin99/netscope.git
cd netscope
npm install
```

### Running in Development

```bash
npm run dev
```

The app starts at `http://localhost:5173`.

### Mock Data Mode

By default, the app runs with mock data so you can explore without a backend. This is controlled by the `VITE_USE_MOCK` environment variable.

| File | Value | Behaviour |
|------|-------|-----------|
| `.env` | `VITE_USE_MOCK=true` | Uses mock data (no backend needed) |
| `.env` | `VITE_USE_MOCK=false` | Connects to FastAPI backend at `/api` |

### Connecting to the Backend

When running with a real backend, the Vite dev server proxies `/api/*` requests to `http://127.0.0.1:8000`. Make sure the FastAPI backend is running on port 8000.

Set `VITE_USE_MOCK=false` in your `.env` file, then start the dev server.

### Building for Production

```bash
npm run build
npm run preview
```

Output goes to the `dist/` directory.

## Default Credentials (Mock Mode)

| Username | Password | Role |
|----------|----------|------|
| `admin` | `NetScope@2024` | NOC Admin |
| `engineer` | `NocEng@2024` | Engineer |

New accounts can be created via the Sign Up form.

## Project Structure

```
src/
├── api/                  # API layer
│   ├── client.js         # Fetch wrapper with auth headers
│   ├── auth.js           # Login, register, session
│   ├── alerts.js         # Alert endpoints
│   ├── devices.js        # Device endpoints
│   ├── system.js         # System status, retrain
│   ├── topology.js       # Building/device topology
│   ├── traffic.js        # Traffic data
│   └── mock/             # Mock data for standalone demo
├── components/
│   ├── layout/
│   │   ├── Shell.jsx     # Sidebar + Topbar wrapper
│   │   ├── Sidebar.jsx   # Navigation with alert badge
│   │   └── Topbar.jsx    # Live status indicator
│   ├── Shared.jsx        # SeverityBadge, HealthScore, PulseStrip, AlertItem
│   ├── StatusBanner.jsx  # Phase-aware system banner
│   ├── NotificationStack.jsx
│   ├── BandwidthChart.jsx
│   └── PacketChart.jsx
├── context/
│   ├── AuthContext.jsx   # Auth state, login/logout, inactivity timer
│   ├── SystemContext.jsx # System status polling
│   └── AlertContext.jsx  # Open alerts + toast notifications
├── hooks/
│   └── usePolling.js     # Generic polling hook
├── pages/
│   ├── Login.jsx         # Login + Sign Up forms
│   ├── Overview.jsx      # Building cards with device lists
│   ├── Devices.jsx       # Device table
│   ├── DeviceDetail.jsx  # Per-device metrics + alerts
│   ├── Alerts.jsx        # Alert history + filters
│   └── Traffic.jsx       # Bandwidth + detector scores
├── utils/
│   └── format.js         # Severity colors, formatters, labels
├── App.jsx               # Router + providers + protected routes
├── main.jsx              # Entry point
└── index.css             # Design tokens + global styles
```

## Backend API Endpoints

The frontend expects these endpoints from the FastAPI backend:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/login` | Authenticate user |
| POST | `/auth/register` | Create new account |
| GET | `/auth/me` | Get current user |
| GET | `/system/status` | System phase + model version |
| POST | `/system/retrain` | Trigger model retrain |
| GET | `/topology/buildings` | Building summaries |
| GET | `/topology/devices` | All devices |
| GET | `/devices/{ip}` | Device detail |
| POST | `/devices/{ip}/baseline` | Train device baseline |
| DELETE | `/devices/{ip}/baseline` | Remove device baseline |
| GET | `/alerts/open` | Currently open alerts |
| GET | `/alerts` | Alert history (filterable) |
| GET | `/alerts/distribution` | Alert distribution stats |
| GET | `/alerts/health-scores` | Per-device health scores |
| GET | `/traffic/recent` | Recent traffic data |
| GET | `/traffic/live-scores` | Live detector scores |

## Security Features

- **Rate limiting** — Account locks for 15 minutes after 5 failed login attempts
- **Inactivity timeout** — Auto-logout after 30 minutes of no interaction
- **Session expiry** — Automatic redirect to login on 401 responses
- **Auth tokens** — JWT Bearer tokens attached to all API requests
- **Protected routes** — All dashboard pages require authentication

## License

This project is part of a final year research project at KNUST.
