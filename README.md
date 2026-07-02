# AI-Powered Network Anomaly Detection & Network Health Dashboard

A fully automated pipeline that collects network telemetry (NetFlow + PRTG),
learns "normal" behavior for your network, detects anomalies across four
dimensions (bandwidth spikes, port scans, device behavior, protocol misuse),
raises classified/severity-ranked alerts, computes per-device health scores,
and exposes everything through a REST API for a dashboard.

The system is **self-bootstrapping**: on first run it collects data for a
period, automatically trains its own models, evaluates them, and switches
itself into live detection — no manual training step required. It then
retrains itself on a schedule, automatically rolling back if a retrain
produces a worse model.

---

## 1. High-Level Architecture

```
                Network Devices (Routers/Switches)
                         |
          +--------------+--------------------+
          |                                    |
   NetFlow Export (UDP)                  PRTG Sensors
          |                                    |
          v                                    v
  collectors/netflow_collector.py    collectors/prtg_collector.py
          |                                    |
          +----------------+-------------------+
                            |
              data/raw/netflow_raw_<date>.csv
              data/raw/prtg_raw_<date>.csv
              (daily-rotated; OR Kafka topics in live mode)
                            |
                            v
            preprocessing/unified_preprocessing.py
        (ONE feature-computation codebase for both
         training [from_csv] and live inference [from_stream])
                            |
              +-------------+-------------+
              |                           |
              v                           v
   training/train_*.py            detectors/ensemble_detector.py
   (Isolation Forest /                   |
    Random Forest)                       v
              |                  alerts/alert_engine.py
              v                  (severity, issue type,
   data/models/*.pkl              dedup, health scores)
   normalization_stats.json              |
              ^                           v
              |                  data/alerts/*.json
   orchestrator/orchestrator.py   data/health_scores.json
   (lifecycle: observation ->            |
    training -> inference,               v
    retraining, rollback)          api/main.py (FastAPI)
                            |             |
                            +-------------+
                                          |
                                          v
                              dashboard/frontend/
                         (React + Vite, served from dist/)
```

---

## 2. The Four Detectors

Each detector is an Isolation Forest (unsupervised) trained on engineered
features. Per `anomaly_features.txt`, only Random Forest and Isolation
Forest are used (Random Forest is supported as a drop-in for the port scan
detector if labelled data becomes available — see `training/train_portscan_model.py`).

| Detector | Entity keyed by | What it catches |
|---|---|---|
| **Bandwidth** | `device_ip` | Sudden traffic spikes, link saturation (via z-scores + interface utilization from PRTG) |
| **Port Scan** | `src_ip` | Many distinct ports/destinations, high SYN ratio, low port entropy |
| **Device Behavior** | `device_ip` | A device's traffic profile (volume, protocol mix, destinations, time-of-day) deviating from its own history |
| **Protocol** | `device_ip` | Unusual protocol mix, KL-divergence from the device's baseline distribution, port/protocol mismatches |

All four share **one** feature-computation codebase
(`preprocessing/unified_preprocessing.py`), each with a `from_csv()` path
(training, reads CSV files/directories) and a `from_stream()` path (live
inference, reads in-memory DataFrames from Kafka). This guarantees training
and live inference compute features identically — the single biggest risk
in this kind of system.

---

## 3. Data Collection

### NetFlow (`collectors/netflow_collector.py`)
- Listens on UDP (default port 2055) for NetFlow v5/v9 exports, or parses a `.pcap` file for offline/synthetic data.
- NetFlow v9 template cache is keyed by **(exporter IP, template_id)** — multiple routers reusing the same template ID with different field layouts won't corrupt each other's parsing.
- Output: daily-rotated CSVs `data/raw/netflow_raw_<YYYY-MM-DD>.csv`. Old days can be deleted independently (supports the 90-day rolling retraining window).
- `--publish-kafka` additionally publishes each flow record as JSON to a Kafka topic (`netflow-raw` by default) for live inference (Phase 3). CSV writing can be disabled with `--no-csv` once Kafka is the only consumer needed.

### PRTG (`collectors/prtg_collector.py`)
- Polls PRTG's `historicdata.json` REST API per configured sensor (traffic in/out, errors, CPU, memory) and merges them into rows matching the exact schema `unified_preprocessing._load_snmp()` expects: `timestamp, device_ip, if_in_octets, if_out_octets, if_speed, if_in_errors, cpu_load_pct, mem_used_pct`.
- `if_speed` comes from `config.yaml` per device (PRTG traffic sensors don't reliably expose nominal link speed as a channel).
- Two modes:
  - `--mode poll`: continuous 60s polling loop (Phase 1-3).
  - `--mode backfill --days N`: one-shot historical pull — if PRTG already has weeks of stored data, backfill it immediately instead of waiting through the observation phase in real time.
- Output: daily-rotated `data/raw/prtg_raw_<YYYY-MM-DD>.csv`, same convention as NetFlow.

**Note on per-flow data**: PRTG only retains interface-level aggregates, not
per-flow records. The port scan and protocol detectors need per-flow detail
(distinct ports/IPs, protocol mix per flow), so `netflow_collector.py` is
kept as a lightweight parallel collector specifically for that data — PRTG
covers bandwidth/CPU/memory/errors, NetFlow covers per-flow detail.

---

## 4. Preprocessing (`preprocessing/unified_preprocessing.py`)

Computes all four detectors' feature sets from raw NetFlow + PRTG data.

Key shared logic:
- **`_assign_device_ip(df)`**: determines which IP in a flow is "the device
  being profiled". If the destination is a private (RFC-1918) address, the
  flow is inbound to a monitored device, so `dst_ip` is the device;
  otherwise `src_ip` is. This ensures inbound traffic from an external
  attacker is attributed to the **internal device being attacked**, not the
  external attacker's IP — critical for the device-behavior and protocol
  detectors to actually see attacks against internal devices.
- **Two feature-computation paths per detector**:
  - `from_csv(...)`: training. Loads CSVs (or directories of daily-rotated
    CSVs), computes rolling z-scores from in-data history
    (`_rolling_zscore`, 1440-window rolling mean/std).
  - `from_stream(...)`: live inference. Takes in-memory DataFrames (from
    Kafka), computes z-scores against **persisted** per-device
    `normalization_stats.json` (`_apply_stats_zscore`) rather than
    recomputing rolling stats from a tiny 1-minute batch — without this,
    live z-scores would always be ~0 regardless of how anomalous traffic
    is, because there's no history in a single Kafka window.
- **`build_all_features()`**: convenience wrapper computing all four
  feature sets from raw CSVs in one call (used by training).
- **`build_all_normalization_stats()`**: computes per-device mean/std for
  the z-score columns, written to `normalization_stats.json` by the
  training scripts and consumed by `from_stream()`.
- **Protocol baseline bootstrapping**: `protocol_baseline.csv` (per-device
  expected protocol mix) doesn't exist on the very first run.
  `_load_baseline()` returns `{}` in that case (KL-divergence = 0 for
  every row on that first run only); `train_protocol_model.py` then writes
  a fresh baseline from that run's data for next time.

---

## 5. Training (`training/`)

Each `train_*.py` script:
1. Computes its feature set via `unified_preprocessing.*.from_csv(...)`
2. Splits train/eval **time-aware** (last 20% of windows by timestamp = eval set — more realistic than a random split for time-series anomaly data)
3. Trains an Isolation Forest (`contamination="auto"` by default)
4. Saves a **model bundle** (`training/common.py:save_model`) containing the
   fitted model, the exact `feature_columns` list (and order!) used for
   training, model type, training row count, and timestamp. This bundle is
   the train/inference contract — live inference rebuilds its feature
   vector using this exact column list via `to_matrix()`, filling any
   missing columns with 0.
5. Writes its slice of `normalization_stats.json`

| Script | Output | Notes |
|---|---|---|
| `train_bandwidth_model.py` | `bandwidth_model.pkl` | |
| `train_portscan_model.py` | `portscan_model.pkl` | Switches to Random Forest automatically if a `label` column is present (for future labelled data e.g. CICIDS2017) |
| `train_device_model.py` | `device_model.pkl` (`--mode global`) or `device_profiles/<ip>_model.pkl` (`--mode per-device --device-ip <ip>`) | Per-device mode implements the on-request "normal baseline" feature |
| `train_protocol_model.py` | `protocol_model.pkl` + refreshes `protocol_baseline.csv` | |

### Evaluation gate (`training/evaluate_models.py`)
After all four scripts run, this checks each model:
- Loads without error, has required bundle keys
- Every `feature_column` the model expects exists in the current processed
  features (catches `unified_preprocessing.py` drifting out of sync with
  already-trained models)
- Evaluation-split scores aren't degenerate (not all-identical, not NaN,
  within `[0,1]`)
- For Random Forest models with labels: eval accuracy ≥ 0.6

Exits non-zero if anything fails — this is the gate the orchestrator uses to
decide whether to promote or roll back a training run.

---

## 6. The Automated Lifecycle (`orchestrator/`)

### Phases
```
OBSERVATION  --(enough data collected)-->  TRAINING  --(eval passed)-->  INFERENCE
                                                |                              |
                                                +--(eval failed, rollback)-----+
                                                                               |
                                          (retrain_interval_days elapses) -----+
                                                                               |
                                                                   back to TRAINING
```

State is persisted in `data/models/system_state.json`
(`orchestrator/system_state.py`):
```json
{
  "phase": "observation" | "training" | "inference",
  "observation_started_at": <epoch>,
  "last_training_result": "passed" | "failed" | null,
  "models_version": <int>,
  "last_retrain_at": <epoch>,
  "notes": "human-readable status for the dashboard"
}
```

### Observation phase
- `SystemOrchestrator.observation_status()` checks two thresholds from
  `config.yaml`'s `bootstrap` section:
  - `min_collection_days` (wall-clock time since `observation_started_at`)
  - `min_netflow_records` (rows currently in `data/raw/netflow_raw_*.csv`)
- Defaults: 14 days, 100,000 records. Two weeks gives at least two full
  weekday/weekend cycles so the model learns that weekend traffic looks
  different from Monday morning traffic.
- **No anomaly detection runs during observation** — the system is purely
  collecting and saving data.
- If PRTG already has weeks of historical data, `prtg_collector.py
  --mode backfill --days N` can pull it immediately, dramatically shortening
  the real-time wait (the NetFlow side still needs live capture unless you
  have pcap captures to feed via `netflow_collector.py --mode pcap`).

### Training phase (`SystemOrchestrator.trigger_training_now()`)
1. **Archive**: copy current `data/models/*.pkl` +
   `normalization_stats.json` + `device_profiles/` to
   `data/models/archive/<timestamp>/` (rollback point)
2. Run all four `train_*.py` scripts as subprocesses (sequential)
3. Run `evaluate_models.py`
4. **Pass** → promote (archive kept as history, `models_version++`,
   transition to INFERENCE)
5. **Fail** → **rollback**: copy the archived (previous) artifacts back
   over the freshly-trained (failing) ones, stay in INFERENCE if a model
   had previously succeeded (`models_version > 0`), or fall back to
   OBSERVATION if this was the very first training attempt (so the system
   keeps collecting and retries automatically)
6. Archive directory pruned to the last 10 snapshots

### Inference phase
- `_retrain_due()` checks `retrain_interval_days` (default 7) against
  `last_retrain_at`. When due, runs the same training pipeline as above
  (archive → train → evaluate → promote/rollback) — i.e. retraining uses
  identical logic to initial training, just triggered on a schedule instead
  of by the observation threshold.

### Per-device baseline (`train_device_baseline(device_ip)`)
Implements the "on user request, create a normal baseline for a particular
device" requirement. This is **additive and isolated**:
- Does NOT go through the archive/evaluate/promote pipeline for the four
  global models
- Writes to `data/models/device_profiles/<ip>_model.pkl` +
  `normalization_stats.json["device_behavior_profiles"][<ip>]`
- A bad per-device profile can't roll back or break the global models
- Live inference (`ensemble_detector.py`) automatically prefers a device's
  per-device profile over the global `device_model.pkl` when one exists

### Running the orchestrator
```bash
# One tick (check phase, act if needed) - for cron/systemd timers
python orchestrator/orchestrator.py

# Force a training run regardless of phase/thresholds
python orchestrator/orchestrator.py --force-train

# Train a per-device baseline directly
python orchestrator/orchestrator.py --device-baseline 10.0.0.5

# OR: continuous in-process scheduler (ticks hourly by default)
python orchestrator/scheduler.py --interval-minutes 60 --run-immediately
```

---

## 7. Live Inference (`detectors/ensemble_detector.py`, `ingestion/`)

### ModelBundle
Loaded once at startup:
- The four global model bundles (`bandwidth_model.pkl`, etc.) — `None` if
  not yet trained (observation phase)
- `normalization_stats.json`
- `protocol_baseline.csv`
- Any per-device profiles (`device_profiles/*.pkl`)

### `score_window(netflow_df, snmp_df, models)`
Runs all four `from_stream()` feature computations and scores each with its
model via `score_isolation_forest` (Isolation Forest `decision_function`,
remapped so **higher = more anomalous**, clipped to `[0,1]`; ~0.5 is
"typical"). Returns one row per `(detector, entity_id, window)`:

```
detector | entity_id | window | anomaly_score | profile_used | features
```

- `anomaly_score` is `NaN` if no model is loaded yet (observation phase) —
  this means "no opinion", never treated as "definitely normal" by the
  alert engine.
- `profile_used` is `"per_device"` for device-behavior rows where a
  per-device baseline exists and was used, `"global"` otherwise.
- `features` carries the row's raw feature values (e.g. `if_util_in/out`)
  for finer-grained issue classification downstream.

### Sliding window + stream router (`ingestion/`)
- `sliding_window.SlidingWindowBuffer`: buffers incoming records (keyed by
  their own `timestamp`, not arrival time) into 60s buckets. A window
  flushes once a strictly-later window has been seen, or after a 10s grace
  period (so the most recent window still flushes even if traffic stops).
- `stream_router.StreamRouter`: the Phase 3 main loop.
  - Consumes Kafka topics `netflow-raw` and `prtg-metrics`
  - `tick()`: flushes ready windows from both buffers, calls
    `process_one_window()` for each
  - `process_one_window()`: `score_window()` →
    `AlertEngine.process_window()` — testable without Kafka
  - **Model hot-reload**: each call checks `system_state.json`'s
    `models_version`; if the orchestrator promoted new models since the
    last check, calls `ModelBundle.reload()`. No restart needed after a
    retrain.

```bash
# Requires kafka-python (optional dependency)
python ingestion/stream_router.py
```

---

## 8. Alerting & Health (`alerts/`)

### Risk scoring (`alerts/risk_scoring.py`) — pure functions
- **`score_to_severity(score)`**: `[0,1]` → `info | low | medium | high | critical` via fixed thresholds (0.55/0.65/0.75/0.85). `NaN` → `info`.
- **`classify_issue_type(detector, features)`**: maps each detector to an issue category:
  | Detector | Issue type |
  |---|---|
  | bandwidth (normal utilization) | `network_congestion` |
  | bandwidth (utilization ≥ 0.85) | `device_capacity` |
  | portscan | `connectivity_security` |
  | device_behavior | `device_environment` |
  | protocol | `network_performance` |
- **`compute_health_score(detector_scores)`**: weighted combination of a
  device's per-detector scores → `0-100` (100 = healthy). Scores below the
  "typical" 0.5 threshold clamp to 100. NaN detectors are excluded and their
  weight redistributed, so a device isn't penalized for detectors that
  haven't been trained yet.

### Alert lifecycle (`alerts/alert_store.py`, `alerts/alert_engine.py`)
- Alerts persist as daily-rotated JSON (`data/alerts/alerts_<date>.json`)
  with `status: open | closed`.
- **Deduplication**: `AlertEngine.process_window()` finds any existing
  `OPEN` alert for `(detector, entity_id)`. If the current window is still
  anomalous, it *extends* that alert (`window_count++`, `max_score` updated,
  severity can escalate but never de-escalate while open). If the score
  drops back to `info`/below `MIN_ALERTABLE_SEVERITY` (`low`), the open
  alert is **closed**.
- `process_window()` returns ALL touched alerts (including closures) so a
  caller can be notified when an issue resolves.
- Rows with `NaN` scores are skipped entirely — neither open nor close an
  alert based on "no opinion".
- **Health scores**: `_update_health_scores()` persists per-device
  `0-100` scores to `data/health_scores.json` using only the **latest**
  window's scores across bandwidth/device_behavior/protocol (portscan
  excluded — its `entity_id` is the suspected scanner, often not a managed
  device).
- **Issue distribution** (`issue_distribution(since, until)`): groups
  alerts by entity with `issue_count`, `max_severity`, and the set of
  `issue_types` seen — backs the "issue distribution view".

---

## 9. Topology & Buildings (`topology/topology_builder.py`)

`TopologyBuilder.building_view()` groups devices by `config.yaml`'s
`building` field (devices with no building → `"Unassigned"`), returning per
building: device count, open issue count, max severity, average health
score, and the full device list (each with health/status/open-issue-count).

`device_detail(ip)` returns a single device's health, open alerts, and
whether it has a per-device behavioral baseline.

---

## 10. REST API (`api/`)

```bash
uvicorn api.main:app --reload --port 8000
# docs at http://localhost:8000/docs
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/system/status` | GET | Current phase, observation progress, training result |
| `/system/retrain` | POST | Manually trigger training (409 if already training) |
| `/devices/{ip}` | GET | Device health, open alerts, per-device profile flag |
| `/devices/{ip}/baseline` | POST | Train a per-device "normal baseline" |
| `/devices/{ip}/baseline` | DELETE | Remove a per-device baseline (revert to global model) |
| `/alerts/open` | GET | All currently-open alerts |
| `/alerts` | GET | Historical alerts with filters: `since`, `until`, `last_hours`, `device_ip`, `building`, `severity`, `status` |
| `/alerts/distribution` | GET | Issue distribution view (per-entity issue counts, max severity, issue types) |
| `/alerts/health-scores` | GET | Current per-device 0-100 health scores |
| `/topology/buildings` | GET | Building-grouped device/health/issue view |
| `/topology/devices` | GET | Flat device list with health/status |
| `/traffic/recent?minutes=N` | GET | Per-device bandwidth aggregates for charts |
| `/traffic/live-scores` | GET | Read-only ensemble scoring preview of the most recent data (doesn't persist alerts) |

All endpoints degrade gracefully during observation phase (empty results /
`NaN`→`null` scores / `"unknown"` device status) rather than erroring.

---

## 11. Configuration (`config.yaml`)

Single config file read by every component (`utils/config_loader.py`):

```yaml
system:
  mode: observation              # managed automatically by the orchestrator
  kafka_bootstrap: "localhost:9092"

prtg:
  base_url: "https://prtg.example.local"
  api_token: ""                  # prefer PRTG_API_TOKEN env var
  poll_interval_sec: 60
  avg_interval_sec: 60
  poll_lag_sec: 30

devices:
  - ip: "10.0.0.1"
    name: "core-router-01"
    building: "HQ"
    sensors:
      traffic_in: 1001
      traffic_out: 1002
      if_speed_bps: 1000000000
      if_errors: 1003
      cpu: 1004
      memory: 1005

bootstrap:
  min_collection_days: 14
  min_netflow_records: 100000
  training_hour_utc: 2
  retrain_interval_days: 7
  rolling_training_window_days: 90

paths:
  netflow_raw_dir: "data/raw"
  prtg_raw_dir: "data/raw"
  processed_dir: "data/processed"
  models_dir: "data/models"
  alerts_dir: "data/alerts"
```

`load_config()` resolves relative paths to absolute (anchored at the project
root), pulls `PRTG_API_TOKEN` from the environment if set, and records the
resolved config path as `cfg["_config_path"]` so components that spawn
subprocesses (the orchestrator) always pass `--config <same file>` —
important when running under a non-default config (e.g. in tests).

---

## 12. Running It End-to-End

### Prerequisites

```bash
# Python backend
pip install -r requirements.txt --break-system-packages

# Dashboard frontend (Node 18+ required)
cd dashboard/frontend
npm install
cd ../..
```

### Step-by-step startup

```bash
# 1. Start collectors (each in its own terminal / process)
python collectors/netflow_collector.py --mode udp --port 2055
python collectors/prtg_collector.py --mode poll

# 2. Start the orchestrator scheduler
#    (manages observation -> training -> inference automatically)
python orchestrator/scheduler.py --interval-minutes 60 --run-immediately

# 3. Start the API
uvicorn api.main:app --port 8000

# 4. Start the dashboard (development server with hot reload)
cd dashboard/frontend && npm run dev
# Open http://localhost:5173
```

After the observation phase completes and training passes, the orchestrator
automatically transitions to inference. Collectors can be restarted with
`--publish-kafka` to also feed the live stream router:

```bash
# Phase 3 — live inference (requires kafka-python + a running Kafka broker)
python collectors/netflow_collector.py --mode udp --publish-kafka
python collectors/prtg_collector.py --mode poll      # PRTG already publishes via its own loop
python ingestion/stream_router.py
```

### Quick demo without real hardware

Drop the provided synthetic data into `data/raw/`, then force-train and
start the API + dashboard:

```bash
cp data/test_fixtures/netflow_raw_2026-06-13.csv data/raw/
cp data/test_fixtures/prtg_raw_2026-06-13.csv data/raw/
python orchestrator/orchestrator.py --force-train
uvicorn api.main:app --port 8000
cd dashboard/frontend && npm run dev
```

---

## 12b. Dashboard (`dashboard/frontend/`)

The dashboard is a React 18 + Vite 5 single-page application that consumes
the FastAPI backend. In development the Vite dev server proxies `/api/*`
to `http://127.0.0.1:8000`, so no CORS configuration is needed.

### Development

```bash
cd dashboard/frontend
npm install
npm run dev          # http://localhost:5173
```

Requires the FastAPI backend to be running on port 8000 at the same time.

### Production build

```bash
cd dashboard/frontend
npm run build        # outputs to dashboard/frontend/dist/
```

Serve `dist/` with any static file server. Because it is an SPA, all
non-asset requests must fall back to `dist/index.html`. The `/api/*`
prefix must be proxied to the FastAPI backend.

**Nginx example**:
```nginx
server {
    listen 80;

    # Proxy API requests to FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Serve the React SPA
    location / {
        root /path/to/dashboard/frontend/dist;
        try_files $uri $uri/ /index.html;
    }
}
```

**Caddy example** (`Caddyfile`):
```
:80 {
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /path/to/dashboard/frontend/dist
        try_files {path} /index.html
        file_server
    }
}
```

### Pages

| Page | Route | What it shows |
|---|---|---|
| **Overview** | `/` | Building-grouped health cards · open alert summary · system phase banner |
| **Devices** | `/devices` | Sortable/filterable table — status, name/IP, building, health score, open issues, model type |
| **Device Detail** | `/devices/:ip` | Health score · per-detector anomaly scores · PulseStrip · open alerts · per-device baseline controls |
| **Alerts** | `/alerts` | Issue distribution cards · filterable alert history (status / severity / detector / time window) |
| **Traffic** | `/traffic` | Per-device bandwidth area charts · latest live anomaly scores |

### System status banner

A persistent banner at the top of every page shows the current lifecycle
phase, the orchestrator's last status note, and a **Retrain now** button.
During observation it also shows a progress bar (time elapsed + record
count toward the configured thresholds).

| Phase | Banner appearance |
|---|---|
| `observation` | Amber pill · progress bar · note e.g. "Collecting baseline data — day 3 of 14" |
| `training` | Blue pill · "Training pipeline running." · button disabled |
| `inference` | Green pill · model version number · Retrain button active |

### Per-device baseline (Device Detail page)

When an admin navigates to a device and clicks **Train baseline for this
device**, the dashboard calls `POST /devices/{ip}/baseline`, which triggers
`train_device_model.py --mode per-device` in the background. The device's
anomaly scoring then uses this personalised model instead of the global one.
The button changes to **Remove baseline** to revert. Both actions complete
synchronously (the API blocks until the subprocess finishes, typically a
few seconds).

### PulseStrip — the signature element

Every device row and the Device Detail page show a **PulseStrip**: a
horizontal strip of small vertical ticks, one per recent health-score
sample, read left (oldest) → right (latest). Tick height and color encode
the severity band of that score:

| Color | Health range | Meaning |
|---|---|---|
| Green `#4ADE80` | 90–100 | Healthy |
| Teal `#3FA796` | 78–89 | Low anomaly |
| Amber `#D9A33E` | 65–77 | Medium anomaly |
| Orange `#E0763C` | 50–64 | High anomaly |
| Red `#E05C5C` | < 50 | Critical anomaly |
| Grey | — | No data yet |

### Polling intervals

All pages poll the API automatically; nothing requires a manual refresh.

| Data | Interval |
|---|---|
| System status (banner) | 10 s |
| Buildings / open alerts | 15 s |
| Device list | 20 s |
| Device detail / per-device alerts | 15 s |
| Traffic charts | 15 s |
| Live anomaly scores | 30 s |

### Design tokens

```
Background:  #0B0E14  (near-black, blue cast — NOC monitor feel)
Panel:       #13171F
Border:      #22272F
Text:        #E6E9EF
Text dim:    #8B92A3
Accent:      #4ADE80  (healthy green)

Severity:    info=#5B7A99  low=#3FA796  medium=#D9A33E  high=#E0763C  critical=#E05C5C

Display font:  IBM Plex Mono  (tabular numerals, data values, IPs)
Body font:     Inter          (UI labels, descriptions)
```

---

## 13. Testing

184 tests across the project:

```bash
pip install -r requirements.txt --break-system-packages
python -m pytest tests/ -v
```

| File | Covers |
|---|---|
| `test_preprocessing.py` | Feature computation, `_assign_device_ip`, empty-input handling, z-score paths |
| `test_collectors.py` | NetFlow v9 multi-exporter template isolation, CSV rotation |
| `test_prtg_collector.py` | PRTG channel parsing, schema contract with `_load_snmp` |
| `test_training.py` | Model save/load contract, feature column selection, evaluation gating |
| `test_orchestrator.py` | Full lifecycle integration: observation→training→inference, rollback, per-device baselines, archive pruning (slow — runs real training subprocesses) |
| `test_ensemble_detector.py` | Live scoring, per-device profile overrides, graceful no-model degradation |
| `test_alerts.py` | Severity/issue classification, health scores, alert dedup/close lifecycle |
| `test_stream_router.py` | Sliding window buffering, end-to-end window processing, model hot-reload |
| `test_api.py` | All REST endpoints against a full trained-model fixture |

---

## 14. Known Tuning Points / Future Work

- **Severity thresholds** (`alerts/risk_scoring.py: SEVERITY_THRESHOLDS`)
  are conservative defaults (0.55-0.85); real alert volume should inform
  tuning — synthetic test data showed quite a bit of `low`-severity churn
  near the 0.5 "typical" boundary.
- **Syslog ingestion** for authentication-failure detection and fault
  detection was planned but not yet implemented — would add a third
  collector (`syslog_listener.py`) and a fifth issue-type category
  (`authentication_failure`).
- **Topology graph edges** (device-to-device links) are not inferred —
  `topology_builder.py` currently returns devices grouped by building as a
  flat list, not a connected graph. Would require LLDP/CDP or routing-table
  discovery.
- **Random Forest for port scan**: supported but unused until labelled data
  (e.g. CICIDS2017/UNSW-NB15, or synthetic scans via Scapy) is supplied via
  `train_portscan_model.py --labelled-csv`.

---

## Mininet School Simulation: SNMP-backed PRTG Emulation

This project now includes a simulation mode for the final-year-project story where the school uses PRTG.
In a real deployment, `collectors/prtg_collector.py` polls the PRTG REST API. In the Mininet lab, there is
usually no PRTG server, so `collectors/snmp_prtg_collector.py` polls SNMP counters from simulated routers and
servers, computes interval deltas, and writes the same PRTG-style files:

```text
 data/raw/prtg_raw_<YYYY-MM-DD>.csv
```

The output schema is unchanged:

```csv
timestamp,device_ip,if_in_octets,if_out_octets,if_speed,if_in_errors,cpu_load_pct,mem_used_pct
```

### Recommended lab layout

```text
Host OS
  - FastAPI backend / dashboard
  - NetFlow UDP collector on 0.0.0.0:2055
  - data/raw shared with Mininet VM, or copied from the VM after collection

Mininet VM
  - Linux routers and OVS switches
  - snmpd running inside monitored router/server namespaces
  - SNMP-backed PRTG emulation collector
  - OVS switches exporting NetFlow to the host-only adapter IP
```

### Run the Mininet topology

Install Mininet-side packages first:

```bash
sudo apt-get update
sudo apt-get install -y snmp snmpd iperf nmap curl
```

Start the topology from the project root inside the Mininet VM. Replace `192.168.56.1` with the host-only IP
of the machine running your anomaly system:

```bash
sudo python3 simulation/mininet_school_topology.py \
  --netflow-target 192.168.56.1:2055 \
  --config-out simulation/mininet_config.generated.yaml
```

The script creates a routed school network, configures OVS NetFlow export, starts `snmpd` in the monitored
namespaces, starts simple HTTP/iperf demo services, and writes `simulation/mininet_config.generated.yaml` with
the correct SNMP management IPs and interface indexes.

### Run the SNMP-backed PRTG collector

In a second terminal inside the Mininet VM:

```bash
python3 collectors/snmp_prtg_collector.py \
  --mode poll \
  --config simulation/mininet_config.generated.yaml \
  --backend cli
```

The first poll initializes counter baselines. Rows are normally written from the second poll onward.
For a quick smoke test that writes zero-delta first rows:

```bash
python3 collectors/snmp_prtg_collector.py \
  --mode once \
  --config simulation/mininet_config.generated.yaml \
  --backend cli \
  --emit-first-sample
```

### Run the NetFlow collector on the host OS

```bash
python collectors/netflow_collector.py --mode udp --host 0.0.0.0 --port 2055
```

### Demo traffic inside the Mininet CLI

Normal baseline traffic:

```bash
finance-pc ping -c 10 10.0.3.10
staff-pc curl http://10.0.3.10/
branch-pc ping -c 10 10.0.1.11
```

Controlled lab anomalies:

```bash
# bandwidth spike inside the lab
finance-pc iperf -c 10.0.3.10 -t 60

# limited internal port-scan simulation against the demo web server
staff-pc nmap -p 1-200 10.0.3.10

# protocol/port misuse signal: UDP traffic on DNS-like port to the web server
finance-pc iperf -u -c 10.0.3.10 -p 53 -b 5M -t 30
```

Keep these commands inside the Mininet lab only. They are designed to generate labelled demonstration traffic
without touching any real external systems.


## Production Security Setup

The dashboard API includes the realistic minimum security controls for a LAN deployment:

- Login authentication using bearer tokens
- Admin-only access for retraining and device-baseline changes
- Optional LAN/IP allow-list middleware
- Strict CORS origin configuration
- Security-sensitive audit logging
- Secrets loaded from environment variables instead of hardcoded source values

Before running the secured API for the first time, create a JWT secret and bootstrap administrator:

```bash
$env:NETSCOPE_JWT_SECRET="change-this-to-a-long-random-secret"
$env:NETSCOPE_BOOTSTRAP_USERNAME="admin"
$env:NETSCOPE_BOOTSTRAP_PASSWORD="change-this-admin-password"
```

Then start the backend:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

For a production LAN deployment, set the allowed dashboard origin and optionally enforce the LAN allow-list:

```bash
export NETSCOPE_CORS_ORIGINS="https://netscope.school.local"
export NETSCOPE_ENFORCE_IP_ALLOWLIST="true"
export NETSCOPE_IP_ALLOWLIST="127.0.0.1/32,192.168.0.0/16,10.0.0.0/8"
```

Audit events are written to `data/audit/audit.log`. User records are stored in `data/security/users.json` with PBKDF2 password hashes.

Use HTTPS through a reverse proxy such as Nginx or Caddy. If TLS is terminated before FastAPI, keep the API bound to localhost or a protected management interface.
