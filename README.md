**AI-Powered Network Anomaly Detection & Network Health Dashboard**

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Data Flow](#4-data-flow)
5. [Configuration Reference](#5-configuration-reference)
6. [Collectors](#6-collectors)
7. [Preprocessing Pipeline](#7-preprocessing-pipeline)
8. [Training Pipeline](#8-training-pipeline)
9. [Orchestrator & Lifecycle](#9-orchestrator--lifecycle)
10. [Live Inference](#10-live-inference)
11. [Alerting System](#11-alerting-system)
12. [Topology & Health](#12-topology--health)
13. [REST API Reference](#13-rest-api-reference)
14. [Dashboard Frontend](#14-dashboard-frontend)


---

## 1. Project Overview

Netscope is an autonomous network monitoring backend that:

- **Collects** raw telemetry from network devices via NetFlow v5/v9 (per-flow records) and PRTG's REST API (interface traffic, CPU, memory, error counters).
- **Learns** what normal looks like for your network automatically — no manual threshold-setting, no labelling required for initial deployment.
- **Detects** four categories of network anomaly using Isolation Forest models trained on engineered features.
- **Classifies** every anomaly with a severity level and a named issue type that maps to standard network operations categories.
- **Alerts** through a deduplicated alert lifecycle (open → update → close) rather than firing a new alert for every anomalous window.
- **Heals itself** by retraining on a schedule, evaluating new models against a held-out split before promoting them, and rolling back automatically if a retrain produces a worse model.
- **Exposes** everything through a FastAPI REST API consumed by a React dashboard.

The system is designed around one central principle: **the training code and the live inference code use the same feature-computation functions**. There is no separate "offline" and "online" preprocessing — both paths call the same Python functions in `unified_preprocessing.py`, differing only in how they read data (CSV vs in-memory DataFrame). This eliminates the most common production failure mode in ML systems: silent feature mismatch between training and serving.

---

## 2. System Architecture

### High-Level Overview

```
┌──────────────────────────────────────────────────────┐
│                  Network Devices                     │
│          Routers · Switches · Access Points          │
└──────────┬──────────────────────────┬───────────────┘
           │ NetFlow v5/v9 UDP        │ PRTG Sensors
           │ (per-flow records)       │ (aggregated metrics)
           ▼                          ▼
┌──────────────────┐       ┌────────────────────────────┐
│ netflow_          │       │ prtg_collector.py          │
│ collector.py      │       │                            │
│                  │       │ Polls PRTG REST API for:   │
│ UDP listener on  │       │  · if_in/out_octets        │
│ port 2055        │       │  · cpu_load_pct            │
│ Parses v5 + v9   │       │  · mem_used_pct            │
│ (per-exporter    │       │  · if_in_errors            │
│  template cache) │       │  · if_speed                │
└────────┬─────────┘       └──────────┬─────────────────┘
         │                            │
         │ CSV (training)  or  Kafka (live inference)
         ▼                            ▼
    data/raw/                  Kafka topics:
    netflow_raw_<date>.csv       netflow-raw
    prtg_raw_<date>.csv          prtg-metrics
         │                            │
         └────────────┬───────────────┘
                      │
         ┌────────────▼────────────────────────────────┐
         │      preprocessing/unified_preprocessing.py  │
         │                                              │
         │  from_csv()  ──────►  Feature DataFrames     │
         │  from_stream()        (training / inference) │
         │                                              │
         │  4 feature classes (one per detector):       │
         │   BandwidthFeatures                          │
         │   PortScanFeatures                           │
         │   DeviceBehaviorFeatures                     │
         │   ProtocolFeatures                           │
         └──────┬──────────────────────────────────────┘
                │
        ┌───────┴───────────────────────┐
        │                               │
        ▼  (training path)              ▼  (inference path)
┌────────────────────┐        ┌─────────────────────────────┐
│  training/         │        │  ingestion/stream_router.py │
│  train_*.py        │        │                             │
│  evaluate_models.py│        │  SlidingWindowBuffer        │
│                    │        │  (60s windows, watermark    │
│  Isolation Forest  │        │   flush on later window or  │
│  or Random Forest  │        │   10s grace period)         │
│                    │        │          │                   │
│  data/models/*.pkl │        │          ▼                   │
│  normalization_    │        │  detectors/                  │
│  stats.json        │        │  ensemble_detector.py       │
└────────┬───────────┘        │                             │
         │                    │  ModelBundle.score_window() │
         │                    │  (all 4 detectors parallel) │
         │                    └─────────────┬───────────────┘
         │                                  │
         └──────────────────────────────────┘
                        │
                        ▼
         ┌──────────────────────────────────┐
         │  orchestrator/orchestrator.py    │
         │                                  │
         │  OBSERVATION → TRAINING →        │
         │  INFERENCE → (retrain)           │
         │                                  │
         │  archive → train → evaluate      │
         │  → promote or rollback           │
         └──────────────┬───────────────────┘
                        │
                        ▼
         ┌──────────────────────────────────┐
         │  alerts/alert_engine.py          │
         │                                  │
         │  score → severity                │
         │  → issue classification          │
         │  → dedup (open/update/close)     │
         │  → health scores                 │
         └──────────────┬───────────────────┘
                        │
               ┌────────┴─────────┐
               ▼                  ▼
    data/alerts/            data/health_scores.json
    alerts_<date>.json
               │
               └──────────────────────────────────┐
                                                  ▼
                                    ┌─────────────────────────┐
                                    │  api/main.py (FastAPI)  │
                                    │                         │
                                    │  /system  /devices      │
                                    │  /alerts  /topology     │
                                    │  /traffic               │
                                    └───────────┬─────────────┘
                                                │
                                                ▼
                                   dashboard/frontend/ (React)
                                   Overview · Devices · Alerts
                                   Traffic · Device Detail
```

### Component Responsibilities

| Component | Responsibility | Stateful? |
|---|---|---|
| `collectors/` | Raw telemetry ingestion to CSV/Kafka | No (append-only write) |
| `preprocessing/` | Feature computation for training and inference | No (pure transforms) |
| `training/` | Model fitting, evaluation gating, normalization stats | No (reads CSVs, writes models) |
| `orchestrator/` | Lifecycle state machine, retraining schedule, rollback | Yes (`system_state.json`) |
| `detectors/` | Live scoring using trained model bundles | No (reads models, scores DataFrames) |
| `ingestion/` | Kafka consume loop, sliding window buffer, per-window dispatch | Yes (in-memory window buffers) |
| `alerts/` | Severity/classification, alert lifecycle, health scores | Yes (`data/alerts/`, `health_scores.json`) |
| `topology/` | Building-grouped views, device status aggregation | No (reads config + alerts) |
| `api/` | REST endpoints for the dashboard | No (reads all the above) |
| `dashboard/frontend/` | React SPA consuming the API | No (browser state only) |

---

## 3. Directory Structure

```
network-anomaly-detection/
│
├── config.yaml                      # Single config for all components
├── requirements.txt
├── README.md
├── DOCUMENTATION.md                 # This file
│
├── collectors/
│   ├── netflow_collector.py         # NetFlow v5/v9 UDP listener + pcap parser
│   └── packet_utils.py             # v5/v9 packet parsing, per-exporter template cache
│
├── collectors/
│   └── prtg_collector.py           # PRTG REST API poller + backfill mode
│
├── preprocessing/
│   └── unified_preprocessing.py    # THE feature computation module:
│                                   #   BandwidthFeatures
│                                   #   PortScanFeatures
│                                   #   DeviceBehaviorFeatures
│                                   #   ProtocolFeatures
│                                   # from_csv() + from_stream() for each
│
├── training/
│   ├── common.py                   # Shared utilities: save/load model bundle,
│   │                               # feature column selection, train/eval split
│   ├── train_bandwidth_model.py
│   ├── train_portscan_model.py     # Supports IF (default) or RF (if labels present)
│   ├── train_device_model.py       # --mode global | --mode per-device --device-ip
│   ├── train_protocol_model.py     # Also generates protocol_baseline.csv
│   └── evaluate_models.py          # Evaluation gate (exits 1 if any model fails)
│
├── orchestrator/
│   ├── system_state.py             # Phase persistence (system_state.json)
│   ├── orchestrator.py             # Lifecycle manager + subprocess runner
│   └── scheduler.py               # APScheduler wrapper (continuous mode)
│
├── detectors/
│   └── ensemble_detector.py        # ModelBundle + score_window()
│
├── ingestion/
│   ├── sliding_window.py           # Timestamp-keyed window buffer
│   └── stream_router.py            # Kafka consume loop + per-window dispatch
│
├── alerts/
│   ├── risk_scoring.py             # score_to_severity, classify_issue_type,
│   │                               # compute_health_score (all pure functions)
│   ├── alert_store.py              # JSON persistence, open/close lifecycle
│   └── alert_engine.py             # process_window(), issue_distribution(),
│                                   # health score persistence
│
├── topology/
│   └── topology_builder.py         # building_view(), device_list(), device_detail()
│
├── api/
│   ├── main.py                     # FastAPI app, CORS, router mounting
│   ├── routes_system.py            # GET /system/status, POST /system/retrain
│   ├── routes_devices.py           # GET /devices/:ip, POST/DELETE /devices/:ip/baseline
│   ├── routes_alerts.py            # GET /alerts, /alerts/open, /alerts/distribution,
│   │                               #     /alerts/health-scores
│   ├── routes_topology.py          # GET /topology/buildings, /topology/devices
│   └── routes_traffic.py           # GET /traffic/recent, /traffic/live-scores
│
├── utils/
│   └── config_loader.py            # load_config(), resolves paths + env vars
│
├── dashboard/frontend/
│   ├── package.json
│   ├── vite.config.js              # Dev server + /api proxy to FastAPI
│   ├── index.html
│   ├── src/
│   │   ├── main.jsx
│   │   ├── App.jsx                 # Shell: sidebar nav, status banner, routes
│   │   ├── index.css               # Design tokens, layout, component styles
│   │   ├── api/client.js           # Thin fetch wrapper for all endpoints
│   │   ├── hooks/usePolling.js     # Auto-refreshing data hook
│   │   ├── utils/format.js         # Severity, bytes, relative-time formatters
│   │   ├── components/
│   │   │   ├── Shared.jsx          # SeverityBadge, StatusDot, HealthScore,
│   │   │   │                       # PulseStrip, EmptyState, ErrorBanner, AlertItem
│   │   │   └── StatusBanner.jsx    # System phase banner with retrain button
│   │   └── pages/
│   │       ├── Overview.jsx        # Building cards + open alerts
│   │       ├── Devices.jsx         # Sortable device table
│   │       ├── DeviceDetail.jsx    # Device health, alerts, baseline controls
│   │       ├── Alerts.jsx          # Issue distribution + alert history
│   │       └── Traffic.jsx         # Bandwidth charts + live scores
│   └── dist/                       # Production build output
│
├── tests/
│   ├── test_preprocessing.py
│   ├── test_collectors.py
│   ├── test_prtg_collector.py
│   ├── test_training.py
│   ├── test_orchestrator.py        # Integration tests (runs real subprocesses)
│   ├── test_ensemble_detector.py
│   ├── test_alerts.py
│   ├── test_stream_router.py
│   └── test_api.py                 # Integration tests (TestClient + real models)
│
└── data/
    ├── raw/                         # Daily-rotated CSVs from collectors
    ├── processed/                   # Feature CSVs from train_*.py runs
    ├── models/                      # *.pkl bundles, normalization_stats.json,
    │   ├── archive/                 # Previous model snapshots for rollback
    │   ├── device_profiles/         # Per-device model bundles
    │   └── system_state.json        # Orchestrator phase persistence
    ├── alerts/                      # Daily-rotated alert JSON files
    ├── health_scores.json           # Latest per-device health scores
    └── test_fixtures/               # Synthetic data for local testing
```

---

## 4. Data Flow

### Training Path

```
netflow_raw_<date>.csv          prtg_raw_<date>.csv
         │                               │
         └──────────┬────────────────────┘
                    │
                    ▼
    unified_preprocessing.*.from_csv()
                    │
         ┌──────────┼─────────────────────────────────┐
         ▼          ▼            ▼                     ▼
  bandwidth_  portscan_   device_behavior_   protocol_
  features    features    features           features
  .csv        .csv        .csv               .csv
         │          │            │                     │
         └──────────┴────────────┴─────────────────────┘
                    │
              train_*.py (one per detector)
                    │
         ┌──────────┼─────────────────────────────────┐
         ▼          ▼            ▼                     ▼
  bandwidth_  portscan_   device_model.   protocol_
  model.pkl   model.pkl   pkl             model.pkl
                    │
              normalization_stats.json
              protocol_baseline.csv
```

### Live Inference Path (Phase 3)

```
Kafka: netflow-raw          Kafka: prtg-metrics
         │                          │
         ▼                          ▼
   SlidingWindowBuffer        SlidingWindowBuffer
   (60s buckets, keyed        (60s buckets)
    by record timestamp)
         │                          │
         └──────────┬───────────────┘
                    │  (flush when window complete)
                    ▼
        stream_router.process_one_window()
                    │
                    ▼
   unified_preprocessing.*.from_stream(
       netflow_df, snmp_df,
       normalization_stats=stats  ← loaded from normalization_stats.json
   )
                    │
                    ▼
   ensemble_detector.score_window()
   → scores_df [detector, entity_id, window, anomaly_score, features]
                    │
                    ▼
   alert_engine.process_window(scores_df)
   → severity classification
   → issue type classification
   → dedup: open / update / close alerts
   → health score update
```

### Key Data Contracts

**netflow_raw_<date>.csv** (produced by `netflow_collector.py`):
```
timestamp, src_ip, dst_ip, src_port, dst_port, protocol,
tcp_flags, packets, bytes, duration_sec
```

**prtg_raw_<date>.csv** (produced by `prtg_collector.py`):
```
timestamp, device_ip, if_in_octets, if_out_octets, if_speed,
if_in_errors, cpu_load_pct, mem_used_pct
```

**Model bundle** (produced by `training/common.py:save_model()`):
```python
{
  "model": <sklearn estimator>,
  "feature_columns": ["col1", "col2", ...],  # exact order matters for scoring
  "model_type": "isolation_forest" | "random_forest",
  "trained_at": <epoch float>,
  "training_rows": <int>,
  # + any extra_meta passed by the training script
}
```

**normalization_stats.json** (produced during training, consumed during live inference):
```json
{
  "bandwidth": {
    "10.0.0.5": { "bw_in_bytes_mean": 7806.0, "bw_in_bytes_std": 4695.0, ... },
    "10.0.0.6": { ... }
  },
  "device_behavior": {
    "10.0.0.5": { "bytes_in_mean": ..., "bytes_in_std": ..., ... }
  },
  "device_behavior_profiles": {
    "10.0.0.5": { ... }  // per-device baseline stats, if trained
  }
}
```

**score_window() output DataFrame**:
```
detector     | entity_id    | window     | anomaly_score | profile_used | features
-------------|--------------|------------|---------------|--------------|--------
"bandwidth"  | "10.0.0.5"  | 1718000060 | 0.6234        | "global"     | {...}
"portscan"   | "203.0.113.5"| 1718000060 | 0.8901        | "global"     | {...}
"device_...  | "10.0.0.5"  | 1718000060 | 0.5512        | "per_device" | {...}
"protocol"   | "10.0.0.5"  | 1718000060 | NaN           | "global"     | {...}
```
`anomaly_score` is `NaN` when no model is loaded (observation phase). This
means "no opinion" — never treated as "definitely normal."

**Alert JSON schema** (one entry in `data/alerts/alerts_<date>.json`):
```json
{
  "id": "<uuid>",
  "detector": "bandwidth | portscan | device_behavior | protocol",
  "entity_id": "<device_ip or suspected scanner IP>",
  "issue_type": "network_congestion | device_capacity | connectivity_security | device_environment | network_performance",
  "severity": "info | low | medium | high | critical",
  "status": "open | closed",
  "first_window": 1718000060,
  "last_window": 1718000180,
  "window_count": 3,
  "max_score": 0.8901,
  "last_score": 0.8412,
  "building": "HQ",
  "device_name": "core-router-01",
  "profile_used": "global | per_device",
  "created_at": 1718000065.32,
  "updated_at": 1718000185.11,
  "closed_at": null
}
```

---

## 5. Configuration Reference

All configuration lives in `config.yaml` at the project root. Every
component reads this file via `utils/config_loader.load_config()`. The
resolved config is also accessible at runtime as `cfg["_config_path"]`
(the absolute path to the file actually used), which the orchestrator
passes to all training subprocesses via `--config` so they always read
from the same file regardless of working directory.

```yaml
# =========================================================================
# System mode — managed automatically by the orchestrator.
# Override only if you need to manually force a phase for debugging.
# =========================================================================
system:
  mode: observation                 # observation | training | inference
  kafka_bootstrap: "localhost:9092" # Kafka broker for live inference (Phase 3)

# =========================================================================
# PRTG connection
# =========================================================================
prtg:
  base_url: "https://prtg.example.local"
  # API token. Prefer the PRTG_API_TOKEN environment variable over
  # hardcoding here to avoid committing credentials.
  api_token: ""
  poll_interval_sec: 60             # How often to poll each sensor
  avg_interval_sec: 60             # PRTG averaging interval (must match poll)
  poll_lag_sec: 30                  # Backward lag to tolerate PRTG sensor delay

# =========================================================================
# Device list
#
# Each entry must have:
#   ip        — the management IP, must match the IPs that appear as
#               src_ip/dst_ip in NetFlow records exported from that device.
#               Mismatches here mean PRTG metrics won't join to NetFlow
#               flows in unified_preprocessing.
#   name      — human-readable label, shown in the dashboard
#   building  — groups devices in the building-grouped view (item 1).
#               Devices with no building are grouped under "Unassigned".
#   sensors   — PRTG sensor IDs. Any sensor can be omitted; missing sensors
#               produce 0/NaN for that column (same as the "no SNMP data"
#               fallback in unified_preprocessing).
# =========================================================================
devices:
  - ip: "10.0.0.1"
    name: "core-router-01"
    building: "HQ"
    sensors:
      traffic_in: 1001              # PRTG sensor ID for inbound traffic (bps)
      traffic_out: 1002             # PRTG sensor ID for outbound traffic (bps)
      if_speed_bps: 1000000000     # Nominal interface speed (from config,
                                    # not PRTG — traffic sensors don't reliably
                                    # expose this as a queryable channel)
      if_errors: 1003               # Error/discard counter sensor
      cpu: 1004                     # CPU utilisation % sensor
      memory: 1005                  # Memory utilisation % sensor

# =========================================================================
# Bootstrap / retraining thresholds
# =========================================================================
bootstrap:
  # Minimum time to spend in observation before attempting first training.
  # 14 days gives at least two weekday/weekend cycles so the model learns
  # that weekend traffic differs from weekday morning traffic.
  min_collection_days: 14

  # Minimum NetFlow records in data/raw/ before training is triggered.
  # 100,000 flows gives enough diversity across devices, protocols, and
  # time-of-day patterns for a reasonable first model.
  min_netflow_records: 100000

  # UTC hour at which the scheduler triggers training (0-23).
  training_hour_utc: 2

  # Days between retrains in inference phase.
  retrain_interval_days: 7

  # How many days of data the rolling retrain window includes.
  # 90 days captures seasonal patterns without confusing the model with
  # very old device configurations that no longer exist.
  rolling_training_window_days: 90

# =========================================================================
# Data paths (relative to project root; resolved to absolute on load)
# =========================================================================
paths:
  netflow_raw_dir: "data/raw"
  prtg_raw_dir: "data/raw"
  processed_dir: "data/processed"
  models_dir: "data/models"
  alerts_dir: "data/alerts"
```

### Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `PRTG_API_TOKEN` | `prtg_collector.py` | Overrides `config.yaml`'s `prtg.api_token` |

---

## 6. Collectors

### 6.1 NetFlow Collector (`collectors/netflow_collector.py`)

Listens on a UDP socket for NetFlow exports from routers/switches and
writes flow records to daily-rotated CSVs.

#### Modes

**UDP mode** (live collection):
```bash
python collectors/netflow_collector.py \
  --mode udp \
  --host 0.0.0.0 \
  --port 2055 \
  [--publish-kafka] \
  [--kafka-bootstrap localhost:9092] \
  [--kafka-topic netflow-raw] \
  [--no-csv]   # disable CSV when Kafka is the only consumer needed
```

**PCAP mode** (offline / synthetic data):
```bash
python collectors/netflow_collector.py \
  --mode pcap \
  --file captures/traffic.pcap
```

#### Output

Daily-rotated files: `data/raw/netflow_raw_<YYYY-MM-DD>.csv`

Files rotate at UTC midnight. The `_load_netflow()` function in
`unified_preprocessing.py` accepts either a single file path or a
directory, concatenating all `netflow_raw_*.csv` files it finds. Old
daily files can be deleted independently (e.g. anything older than 90
days for the rolling retrain window).

#### NetFlow v9 multi-exporter safety

NetFlow v9 exporters send *template FlowSets* that describe their field
layouts before sending data. Different devices (routers from different
vendors, or even different models from the same vendor) commonly reuse the
same template ID numbers with completely different field layouts.

The template cache in `packet_utils.py` is keyed by
`(source_addr, template_id)` — the exporter's IP address plus the
template ID — rather than template ID alone. Without this,
`core-router-01`'s template 256 would silently overwrite
`branch-router-01`'s template 256, and all of `branch-router-01`'s flows
would be parsed with the wrong field layout, producing garbage data with
no error raised.

#### Kafka publishing

When `--publish-kafka` is passed, each flow record is published to the
Kafka topic as a JSON object matching `NetFlowRecord.to_csv_row()`. The
`kafka-python` package is imported only when this flag is present, so
training-only environments don't need it installed.

### 6.2 PRTG Collector (`collectors/prtg_collector.py`)

Queries PRTG's `historicdata.json` REST API per configured sensor and
produces rows matching the exact schema `_load_snmp()` expects.

#### Why PRTG instead of raw SNMP

PRTG is already deployed on the monitored devices. Writing our own SNMP
poller would duplicate PRTG's device discovery, OID mapping, credential
management, and polling logic. Instead we consume PRTG's clean REST API
output and translate it into the schema the preprocessing module expects.

#### The schema contract

This is the most important design constraint in the PRTG collector: its
output **must** match these exact columns, or `BandwidthFeatures` and
`DeviceBehaviorFeatures` will silently produce wrong results:

```
timestamp, device_ip, if_in_octets, if_out_octets, if_speed,
if_in_errors, cpu_load_pct, mem_used_pct
```

`if_speed` is read from `config.yaml`'s `sensors.if_speed_bps` rather than
from PRTG because PRTG's traffic sensor types don't expose nominal link
speed reliably as a readable channel.

#### Channel name matching

PRTG sensor channel names vary by sensor type, PRTG version, and
localization settings. The collector uses a candidate-list approach
(`CHANNEL_CANDIDATES` dict in `prtg_collector.py`): for each metric, it
tries a list of known channel name variations in order and takes the first
match. The raw numeric value (`<channel_name>_raw`) is always preferred
over the formatted string.

To add support for a custom PRTG sensor name, add it to the appropriate
list in `CHANNEL_CANDIDATES`.

#### Modes

**Poll mode** (live collection):
```bash
python collectors/prtg_collector.py --mode poll
```

**Backfill mode** (historical pull):
```bash
# Pull the last 30 days of history in 24h chunks
python collectors/prtg_collector.py --mode backfill --days 30
```

Backfill is useful when PRTG already has weeks of stored history. Rather
than waiting through the 14-day real-time observation phase, you can
backfill 14+ days immediately, then trigger training via
`python orchestrator/orchestrator.py --force-train`.

---

## 7. Preprocessing Pipeline

### 7.1 The Unified Preprocessing Module (`preprocessing/unified_preprocessing.py`)

This is the most critical module in the system. It defines what "features"
the models see, and it guarantees that training and live inference see
exactly the same features.

#### Structure

Each of the four detectors has its own class with two class methods:
- `from_csv(netflow_csv, ...)` — loads CSVs, computes features for training
- `from_stream(netflow_df, ...)` — takes in-memory DataFrames, for live inference

The actual feature mathematics live in a private `_compute()` method that
both `from_csv` and `from_stream` call. The only difference between the
two paths is how data is loaded.

```
from_csv  ──► _load_netflow() ──► _compute() ──► feature DataFrame
              _load_snmp()

from_stream ──────────────────► _compute() ──► feature DataFrame
              (pre-loaded DataFrames passed in)
```

#### The `_assign_device_ip()` problem

When a flow arrives from external source `203.0.113.50` to internal
destination `10.0.0.5`, naively setting `device_ip = src_ip` would
attribute this traffic to the external attacker's IP — meaning the internal
device `10.0.0.5` would never see the attack traffic in its behavioral
profile.

The shared helper `_assign_device_ip(df)` applies this rule:
- If `dst_ip` is a private (RFC-1918) address → the flow is *inbound to*
  an internal device → `device_ip = dst_ip`
- Otherwise → the flow is *outbound from* an internal device →
  `device_ip = src_ip`

This function is shared by `BandwidthFeatures`, `DeviceBehaviorFeatures`,
and `ProtocolFeatures`. `PortScanFeatures` uses `src_ip` explicitly because
it profiles the scanning *source*, not the target.

#### Z-score architecture

Features like "current bandwidth vs. historical mean" require historical
context. The training and inference paths handle this differently:

**Training** (`from_csv`): `_rolling_zscore()` computes a rolling mean and
standard deviation over 1440 windows (1 day) of in-CSV history, using
`pandas.rolling()`. This works because the training CSV contains days or
weeks of history for every device.

**Live inference** (`from_stream`): A single 1-minute Kafka window has no
history to roll over. Instead, `from_stream` accepts a
`normalization_stats` dict (loaded from `normalization_stats.json`) and
calls `_apply_stats_zscore()`, which looks up each device's pre-computed
mean and std and applies `z = (value - mean) / std` per row.

`normalization_stats.json` is written by the training scripts after every
successful training run. It must be updated alongside the model files —
`build_all_normalization_stats()` handles this.

Without this separation, live z-scores would always be approximately 0
(because there's only one row of "history" in a single Kafka batch), making
the bandwidth spike detector's z-score features useless in production.

#### Empty input safety

All four `from_stream` methods handle empty input DataFrames (no flows
for a particular window) without crashing. The root bug this guards
against: `.apply(_is_private_ip)` on an empty Pandas Series returns
`dtype=object` instead of `bool`, and boolean-indexing a DataFrame with an
object-dtype empty Series silently drops all columns (a silent data
corruption, not an error). The fix: `.astype(bool)` after every
`.apply(_is_private_ip)` call.

#### Feature sets

**BandwidthFeatures** (keyed by `device_ip`, `window`):
```
bw_in_bytes, bw_out_bytes, bw_in_pkts, bw_out_pkts,
bw_in_rate_bps, bw_out_rate_bps,
bw_in_zscore, bw_out_zscore,       ← vs. device's historical baseline
if_util_in, if_util_out,            ← from PRTG (octets / speed)
if_errors_delta,                    ← from PRTG
cpu_load_pct, mem_used_pct          ← from PRTG
```

**PortScanFeatures** (keyed by `src_ip`, `window`):
```
flows_total, flows_per_sec,
distinct_dst_ports, distinct_dst_ips,
port_entropy,                        ← low = sequential scan, high = random
tcp_syn_ratio, udp_ratio,
success_rate,                        ← flows with established flags / total
small_flow_ratio,                    ← flows < 3s duration
well_known_port_ratio                ← ports 1-1024
```

**DeviceBehaviorFeatures** (keyed by `device_ip`, `window`):
```
bytes_in, bytes_out,
bytes_in_zscore, bytes_out_zscore,
tcp_ratio, udp_ratio, icmp_ratio,
distinct_dst_ips, distinct_dst_ips_zscore,
hour_sin, hour_cos,                  ← cyclical time encoding
cpu_util_zscore, mem_util_zscore     ← from PRTG
```

**ProtocolFeatures** (keyed by `device_ip`, `window`):
```
protocol_entropy,
tcp_ratio, udp_ratio, icmp_ratio, other_ratio,
num_new_protocols,                   ← protocols not in device's baseline
port_protocol_mismatch_count,        ← e.g. TCP to port 53 (should be UDP)
avg_pkt_size_tcp, avg_pkt_size_udp,
kl_div_from_baseline                 ← KL divergence from device's historical
                                       protocol distribution
```

### 7.2 Loading Raw Data

`_load_netflow(path)` and `_load_snmp(path)` both accept either:
- A single CSV file path, or
- A directory, in which case all matching `netflow_raw_*.csv` or
  `prtg_raw_*.csv` files are concatenated.

This supports the daily-rotation convention the collectors use — the
preprocessing module never needs to know which day's files to load; it
just reads everything in the directory.

---

## 8. Training Pipeline

### 8.1 Overview

Training scripts are intentionally simple, dependency-light processes:
they read CSVs, compute features, fit a model, and write files. No Kafka,
no live collectors, no orchestrator logic. This makes them easy to test
in isolation and easy to debug when a retrain fails.

The orchestrator triggers them as subprocesses, so a crash in a training
script is cleanly isolated and the orchestrator can roll back the previous
models without any corruption of the running inference pipeline.

### 8.2 Model Bundle Format

The most important artifact each script produces is a model bundle — a
dict serialised by `joblib.dump()`:

```python
{
  "model": <fitted sklearn estimator>,
  "feature_columns": ["col_a", "col_b", ...],  # CRITICAL: exact column order
  "model_type": "isolation_forest",
  "trained_at": 1718000000.0,
  "training_rows": 12450,
  # any extra_meta passed to save_model()
}
```

The `feature_columns` list is the train/inference contract. When
`score_window()` calls `to_matrix(feat_df, bundle["feature_columns"])`,
it builds the feature matrix in this exact column order. If a column is
missing from the live feature DataFrame (e.g. no UDP flows this window →
`avg_pkt_size_udp` is absent), `to_matrix()` fills it with 0, matching
the `fillna(0)` convention used at the end of every `_compute()` call.

If `unified_preprocessing.py` ever adds, removes, or renames a feature
column, re-running `evaluate_models.py` will catch the mismatch:

```
FAIL: Model expects feature columns not present in current processed features:
['old_feature_col']. This usually means unified_preprocessing.py changed
its output columns since this model was trained.
```

### 8.3 Time-Aware Train/Eval Split

`split_train_eval()` in `training/common.py` sorts by the `window` column
and puts the last 20% of windows into the evaluation set, not a random
sample. This is more realistic for time-series anomaly data: the evaluation
set resembles "the immediate future after training," which is what live
inference will face.

A random split would allow the model to "see" future patterns during
training (temporal leakage), producing overly optimistic evaluation scores.

### 8.4 Isolation Forest Scoring Convention

`score_isolation_forest()` in `training/common.py` transforms sklearn's
`decision_function` output (which is positive for normal points, negative
for anomalies) into an intuitive `[0, 1]` range where higher = more
anomalous:

```python
scores = clip(0.5 - decision_function(X), 0, 1)
```

This means:
- A normal point (decision_function ≈ 0) → anomaly score ≈ 0.5
- A very anomalous point (decision_function ≈ -0.5) → anomaly score ≈ 1.0
- A very "normal" point (decision_function ≈ +0.5) → anomaly score ≈ 0.0

The severity thresholds (0.55, 0.65, 0.75, 0.85) are calibrated relative
to this 0.5 "typical normal" baseline.

### 8.5 Protocol Baseline Bootstrapping

`train_protocol_model.py` handles a chicken-and-egg problem:
`ProtocolFeatures` needs a per-device protocol baseline to compute
`kl_div_from_baseline` and `num_new_protocols`, but this baseline itself
comes from the training data.

The bootstrap sequence:
1. **First run**: `protocol_baseline.csv` doesn't exist. `_load_baseline()`
   returns `{}` → KL divergence and `num_new_protocols` are 0 for every
   row. The model trains on these zero-filled values. At the end of the run,
   a fresh `protocol_baseline.csv` is written from the training data.
2. **Subsequent runs**: `protocol_baseline.csv` exists and is loaded before
   feature computation. KL divergence now reflects real protocol drift since
   the last training run. The baseline is refreshed again at the end.

This means the protocol detector is effectively "blind" to protocol drift
on its very first training run, but fully functional on all subsequent ones.

### 8.6 Per-Device Baselines

`train_device_model.py --mode per-device --device-ip <ip>` trains a
dedicated Isolation Forest on only that device's historical windows. The
result is stored in `data/models/device_profiles/<ip>_model.pkl`.

This implements the "on user request, create a normal baseline for a
particular device" requirement. An admin triggers this from the dashboard
(Device Detail page → "Train baseline for this device"), which calls
`POST /devices/<ip>/baseline`, which calls
`SystemOrchestrator.train_device_baseline(ip)`.

Per-device models are **additive and isolated** — they do not go through
the archive/evaluate/promote pipeline used for the four global models. A
bad per-device profile cannot roll back or break the global models.

### 8.7 Evaluation Gate (`evaluate_models.py`)

The evaluation gate is the orchestrator's final check before promoting
new models. It fails (exits 1) if any of:

- A model file doesn't exist or can't be loaded
- A model's `feature_columns` includes columns not present in the current
  processed features (catches preprocessing drift)
- Evaluation-split anomaly scores are all identical (degenerate model)
- Evaluation scores contain NaN
- Evaluation scores fall outside `[0, 1]`
- For Random Forest models with labels: accuracy below 0.6

On failure, the orchestrator rolls back to the previously archived models.
The system either stays in INFERENCE (serving the old models) or returns to
OBSERVATION (if this was the first-ever training attempt).

---

## 9. Orchestrator & Lifecycle

### 9.1 Phase State Machine

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  OBSERVATION                                                │
│  - Collectors write to data/raw/                           │
│  - No detection runs                                        │
│  - tick() checks thresholds every interval                  │
│                                                             │
│  → Exits when:                                              │
│     min_collection_days elapsed AND                         │
│     netflow record count ≥ min_netflow_records             │
│                                                             │
└────────────────────────────┬────────────────────────────────┘
                             │ thresholds met
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  TRAINING  (transient, typically seconds to minutes)        │
│  1. Archive current models → data/models/archive/<ts>/     │
│  2. Run train_bandwidth_model.py                           │
│  3. Run train_portscan_model.py                            │
│  4. Run train_device_model.py                              │
│  5. Run train_protocol_model.py                            │
│  6. Run evaluate_models.py                                 │
│                                                             │
│  → Pass: promote, transition to INFERENCE                   │
│  → Fail: rollback archived models                          │
│          if models_version > 0 → stay in INFERENCE          │
│          if models_version == 0 → return to OBSERVATION     │
│                                                             │
└────────────────────────────┬────────────────────────────────┘
                             │ eval passed
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  INFERENCE                                                  │
│  - Live detection running (stream_router.py)               │
│  - tick() checks retrain_interval_days each interval       │
│  - Models hot-reload when models_version changes           │
│                                                             │
│  → Exits to TRAINING when:                                  │
│     (now - last_retrain_at) ≥ retrain_interval_days × 86400│
│     OR admin calls POST /system/retrain                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 System State

`orchestrator/system_state.py` wraps a single JSON file
(`data/models/system_state.json`) with atomic writes (write to `.tmp`,
then `rename()` — POSIX-atomic):

```json
{
  "phase": "inference",
  "observation_started_at": 1718000000.0,
  "last_training_started_at": 1719209200.0,
  "last_training_completed_at": 1719209845.0,
  "last_training_result": "passed",
  "last_retrain_at": 1719209845.0,
  "models_version": 2,
  "notes": "Training completed and promoted (snapshot: 2024-06-24T02-10-45Z)."
}
```

This file is read by:
- `orchestrator.py` (writes and reads it)
- `stream_router.py` (reads `models_version` to detect when to hot-reload)
- `api/routes_system.py` (reads it to serve the `/system/status` endpoint)
- `dashboard/frontend` (consumes `/system/status` to render the phase banner)

### 9.3 Archive and Rollback

Before every training run, `_archive_current_models()` copies:
```
data/models/bandwidth_model.pkl
data/models/portscan_model.pkl
data/models/device_model.pkl
data/models/protocol_model.pkl
data/models/normalization_stats.json
data/models/device_profiles/  (directory copy)
```
to `data/models/archive/<YYYY-MM-DDTHH-MM-SSZ>/`.

If `evaluate_models.py` exits non-zero, `_rollback()` copies these files
back over whatever the training scripts just produced. This is the complete
rollback — the inference pipeline will automatically use the restored files
on its next `models_version` check.

Archives are pruned to the most recent 10 snapshots (configurable via
`ARCHIVE_RETENTION` in `orchestrator.py`).

### 9.4 Running the Orchestrator

**One-off tick** (for cron/systemd timers):
```bash
python orchestrator/orchestrator.py
```

**Forced training** (ignores thresholds):
```bash
python orchestrator/orchestrator.py --force-train
```

**Per-device baseline** (runs in-process, no subprocess):
```bash
python orchestrator/orchestrator.py --device-baseline 10.0.0.5
```

**Continuous scheduler** (checks every N minutes):
```bash
python orchestrator/scheduler.py --interval-minutes 60 --run-immediately
```

The scheduler uses APScheduler's `BlockingScheduler` with `max_instances=1`
so if a training run is still executing when the next tick fires, the tick
is skipped rather than starting a second concurrent training run.

---

## 10. Live Inference

### 10.1 Sliding Window Buffer (`ingestion/sliding_window.py`)

`SlidingWindowBuffer` implements a watermark-style window buffer:

- Records are added with `add(record)`, where `record` must have a numeric
  `timestamp` key (the flow's own timestamp, not arrival time).
- Records are bucketed by `floor(timestamp / window_sec) * window_sec`.
- A window is "ready" (returned by `flush_ready()`) when either:
  - A record from a strictly *later* window has arrived (we know no more
    records for this window will come from the same timeline), or
  - `grace_period_sec` seconds have elapsed since the last new-maximum-window
    was seen (handles quiet periods where traffic stops and no "later"
    record ever arrives).

This is standard stream-processing watermark logic. The grace period
(default 10s) bounds the worst-case latency for the final window of a
session, preventing it from staying in the buffer indefinitely.

### 10.2 Stream Router (`ingestion/stream_router.py`)

`StreamRouter` holds one `SlidingWindowBuffer` for NetFlow records and one
for PRTG records. It processes one window at a time:

```python
def process_one_window(self, window, netflow_records, snmp_records):
    self._maybe_reload_models()        # check models_version
    netflow_df = _records_to_df(netflow_records, NETFLOW_COLUMNS)
    snmp_df = _records_to_df(snmp_records, PRTG_COLUMNS)
    scores_df = score_window(netflow_df, snmp_df, self.models)
    self.alert_engine.process_window(scores_df)
```

`tick()` calls `flush_ready()` on both buffers and processes every window
that has complete data from at least one stream. If a window has NetFlow
but no PRTG data (e.g. the PRTG poller was briefly down), it still
processes — `from_stream()` handles empty `snmp_df` gracefully by filling
PRTG-derived features with 0/NaN.

### 10.3 Model Hot-Reload

`_maybe_reload_models()` is called before every window processing. It reads
`system_state.json`'s `models_version` field and compares it to the last
seen version. If it has changed (the orchestrator just promoted a new
training run), `ModelBundle.reload()` is called to load the new `.pkl`
files from disk.

This means `stream_router.py` does not need to be restarted after a
retrain. The new models are picked up automatically within one window
(60 seconds) of the orchestrator completing promotion.

### 10.4 Starting the Live Inference Loop

```bash
# Prerequisites: kafka-python installed, Kafka broker running
pip install kafka-python --break-system-packages

# Collectors must be running with --publish-kafka
python collectors/netflow_collector.py --mode udp --publish-kafka
python collectors/prtg_collector.py --mode poll

# Then start the stream router
python ingestion/stream_router.py
```

---

## 11. Alerting System

### 11.1 Risk Scoring (`alerts/risk_scoring.py`)

All functions in this module are pure (no I/O, no state).

**`score_to_severity(score)`** maps `[0, 1]` to severity:

| Score range | Severity |
|---|---|
| ≥ 0.85 | critical |
| ≥ 0.75 | high |
| ≥ 0.65 | medium |
| ≥ 0.55 | low |
| < 0.55 | info |
| NaN / None | info |

**`classify_issue_type(detector, feature_row)`**:

| Detector | Default issue type | Refinement |
|---|---|---|
| `bandwidth` | `network_congestion` | → `device_capacity` if `max(if_util_in, if_util_out) ≥ 0.85` |
| `portscan` | `connectivity_security` | — |
| `device_behavior` | `device_environment` | — |
| `protocol` | `network_performance` | — |

The bandwidth congestion/capacity distinction is important operationally:
"congestion" (spike above baseline but link not saturated) suggests a
traffic burst or DDoS; "capacity" (link utilisation ≥ 85%) suggests a
provisioning problem.

**`compute_health_score(detector_scores)`** produces a 0-100 score:
```
weighted_anomaly = Σ(score_i × weight_i) / Σ(weight_i)

health = 100 × (1 - (weighted_anomaly - 0.5) / 0.5)  clamped to [0, 100]
```
Weights: `device_behavior=0.3`, `protocol=0.3`, `bandwidth=0.2`,
`portscan=0.2`. Device behavior and protocol anomalies are weighted more
heavily because they typically indicate compromise or misconfiguration,
which is more serious than a transient traffic spike. NaN scores are
excluded from the weighted average (their weight is redistributed
proportionally).

### 11.2 Alert Store (`alerts/alert_store.py`)

Alerts are stored as JSON files, one per UTC day:
`data/alerts/alerts_<YYYY-MM-DD>.json`. Each file is an array of alert
objects. Updates are written atomically (write `.tmp`, then `rename()`).

An open alert from day N that is still open on day N+3 stays in the file
for day N — it's looked up by scanning the most recent
`_RECENT_DAYS_TO_SCAN = 14` days' files. This bounds the scan window to a
reasonable length (an alert shouldn't stay open for more than 14 days in
practice) while keeping each day's file independent.

### 11.3 Alert Engine (`alerts/alert_engine.py`)

`process_window(scores_df)` implements the dedup logic:

```
For each row in scores_df:
  if score is NaN → skip (no opinion)
  severity = score_to_severity(score)
  existing = store.find_open_alert(detector, entity_id)

  if severity < MIN_ALERTABLE_SEVERITY ("low"):
    if existing → close it
    continue

  if existing:
    store.update_alert(existing, window, score, severity)
    # severity can escalate (new severity > current) but never de-escalate
    # while open — de-escalation means the alert should be closed, not
    # downgraded
  else:
    store.create_alert(detector, entity_id, issue_type, severity, ...)
```

`process_window()` returns all touched alerts (both opened/updated and
closed) so the stream router can log them, and in the future, a
notification system could be triggered on the return value.

---

## 12. Topology & Health

### 12.1 Topology Builder (`topology/topology_builder.py`)

`TopologyBuilder` reads `config.yaml`'s device list, the current
`health_scores.json`, and the open alerts from `AlertStore`, and assembles
views without any additional I/O or computation.

`building_view()` groups devices by their `building` field. For each
building it computes:
- `device_count`
- `open_issue_count` (sum across all devices in the building)
- `max_severity` (worst alert severity across any device in the building)
- `avg_health_score` (mean of all devices with a health score; null if none)
- `devices` (full device nodes for the building)

Devices with no `building` set are grouped under `"Unassigned"`.

`device_detail(ip)` adds two fields to the device node not present in the
list view: `open_alerts` (the full alert objects, for the detail panel) and
`has_per_device_profile` (whether
`data/models/device_profiles/<ip>_model.pkl` exists).

---

## 13. REST API Reference

The API is a FastAPI application (`api/main.py`) with five routers mounted
at `/system`, `/devices`, `/alerts`, `/topology`, `/traffic`.

Interactive documentation (Swagger UI) is available at
`http://localhost:8000/docs` when the server is running.

All endpoints degrade gracefully during the observation phase: empty
collections, `null` scores, and `"unknown"` device status are returned
rather than errors.

### Authentication

The API has no authentication built in. In production, place it behind an
nginx/Caddy reverse proxy or VPN — it is not designed to be exposed
directly to the public internet.

### Endpoints

#### System

| Method | Path | Description |
|---|---|---|
| GET | `/system/status` | Current phase, observation progress, training result, model version |
| POST | `/system/retrain` | Manually trigger the training pipeline. Returns 409 if training is already in progress. Blocks until complete. |

**GET `/system/status` response**:
```json
{
  "phase": "inference",
  "notes": "Training completed and promoted (snapshot: 2024-06-24T02-10-45Z).",
  "models_version": 3,
  "last_retrain_at": 1719209845.0,
  "last_training_result": "passed",
  "observation": {
    "ready": false,
    "days_elapsed": 3.5,
    "days_required": 14,
    "netflow_records": 42150,
    "records_required": 100000
  }
}
```

#### Devices

| Method | Path | Description |
|---|---|---|
| GET | `/devices/{ip}` | Device health, open alerts, per-device profile flag. 404 if not in config.yaml. |
| POST | `/devices/{ip}/baseline` | Train a per-device behavioral baseline. Blocks until complete (~seconds). Returns updated device detail. |
| DELETE | `/devices/{ip}/baseline` | Remove a per-device baseline. Returns 404 if no baseline exists. |

#### Alerts

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/alerts/open` | — | All currently-open alerts |
| GET | `/alerts` | `since`, `until`, `last_hours`, `device_ip`, `building`, `severity`, `status` | Historical query with filters. `last_hours` is a convenience shortcut for `since = now - last_hours * 3600`. |
| GET | `/alerts/distribution` | `since`, `until`, `last_hours=24` | Per-entity issue distribution: count, max severity, issue types. |
| GET | `/alerts/health-scores` | — | Current per-device 0-100 health scores |

**GET `/alerts/distribution` response**:
```json
{
  "since": 1719123600.0,
  "until": null,
  "distribution": [
    {
      "entity_id": "10.0.0.5",
      "building": "HQ",
      "device_name": "core-router-01",
      "issue_count": 4,
      "max_severity": "high",
      "issue_types": ["device_environment", "network_congestion"]
    }
  ]
}
```

#### Topology

| Method | Path | Description |
|---|---|---|
| GET | `/topology/buildings` | Building-grouped device health. One entry per building with device list, issue counts, avg health. |
| GET | `/topology/devices` | Flat list of all configured devices with current health/status. |

#### Traffic

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/traffic/recent` | `minutes=15` (1-1440) | Per-device per-minute bandwidth aggregates. Returns `{window_sec: 60, devices: {ip: [{window, bytes_in, bytes_out, packets_in, packets_out}]}}`. |
| GET | `/traffic/live-scores` | `minutes=1` (1-10) | Ensemble detector scores on the most recent N minutes of data. Does NOT persist alerts. NaN scores serialised as `null`. |

---

## 14. Dashboard Frontend

### 14.1 Technology

| Layer | Choice | Rationale |
|---|---|---|
| Framework | React 18 | Component model suits a live-updating dashboard; hooks make polling clean |
| Build tool | Vite 5 | Fast HMR in dev; no config needed for SPA routing in prod |
| Routing | React Router 6 | Client-side navigation between pages |
| Charts | Recharts | Thin wrapper over D3 that works well with React's render model |
| Fonts | IBM Plex Mono + Inter | Mono for all numeric/data values (tabular numerals align in columns); Inter for prose/labels |
| No CSS framework | Intentional | Design token variables in `index.css` give precise control without fighting a utility framework |

### 14.2 Design System

All visual tokens are CSS custom properties in `src/index.css`:

```
Background:     --bg:        #0B0E14  (near-black, blue cast)
Panel surface:  --panel:     #13171F
Hover surface:  --panel-alt: #181D27
Border:         --border:    #22272F
Primary text:   --text:      #E6E9EF
Secondary text: --text-dim:  #8B92A3
Healthy accent: --accent:    #4ADE80

Severity:
  --sev-info:     #5B7A99  (slate blue)
  --sev-low:      #3FA796  (teal)
  --sev-medium:   #D9A33E  (amber)
  --sev-high:     #E0763C  (orange)
  --sev-critical: #E05C5C  (red)
```

The five severity colors exactly match the API's severity scale, so a
backend alert's `severity: "high"` maps directly to `var(--sev-high)`
everywhere in the UI without any translation table.

### 14.3 Data Fetching

`src/hooks/usePolling.js` provides a `usePolling(fetcher, intervalMs)` hook
that:
- Calls `fetcher()` immediately on mount
- Re-calls it every `intervalMs`
- Returns `{ data, error, loading, refresh }` where `refresh()` is for
  on-demand re-fetches (e.g. after triggering a retrain)
- Errors do NOT clear previously-loaded `data` — a transient API hiccup
  shows `error` alongside the last-known-good data, rather than blanking
  the dashboard

### 14.4 The PulseStrip Component

`PulseStrip` is the dashboard's signature visual element: a horizontal row
of small vertical ticks, one per health-score sample, colored by severity:

```jsx
<PulseStrip samples={[95, 91, 88, 72, 65, 41, 38, 91, 94, 96]} width={40} />
```

Tick height encodes magnitude (healthy = tall, critical = short) and tick
color encodes the severity band. Reading left to right, it looks like a
patient-monitor heartbeat strip, directly embodying the "network health
vital signs" concept.

`healthToSeverityClass(score)` in `src/utils/format.js` maps health scores
to severity class names:
```
90-100 → sev-healthy  (#4ADE80)
78-89  → sev-low      (#3FA796)
65-77  → sev-medium   (#D9A33E)
50-64  → sev-high     (#E0763C)
< 50   → sev-critical (#E05C5C)
null   → sev-unknown  (#22272F)
```

### 14.5 API Proxy Configuration

`vite.config.js` proxies `/api/*` to `http://127.0.0.1:8000` and strips
the `/api` prefix, so `fetch('/api/system/status')` in the frontend reaches
`http://127.0.0.1:8000/system/status` on the backend. This means CORS is
never needed in development.

In production, configure the reverse proxy to do the same prefix-strip (see
the nginx/Caddy examples in README.md section 12b).

---
