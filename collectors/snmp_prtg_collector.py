#!/usr/bin/env python3
"""
snmp_prtg_collector.py
----------------------
Python 3.5-compatible SNMP-backed PRTG-style collector for the Mininet
simulation.

It polls SNMP interface counters from simulated routers/servers, converts
cumulative counters into per-interval deltas, and writes the same CSV schema
as the real PRTG collector:

    timestamp,device_ip,if_in_octets,if_out_octets,if_speed,
    if_in_errors,cpu_load_pct,mem_used_pct

Typical Mininet usage:

  python3 collectors/snmp_prtg_collector.py \
    --mode poll \
    --config simulation/mininet_config.generated.yaml \
    --backend cli

This file intentionally avoids Python 3.6+ features so it can run on older
Mininet VMs that ship with Python 3.5.
"""

import argparse
import csv
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def load_config(config_path=None):
    """Load YAML/JSON config without importing the main project.

    This collector runs inside the Mininet VM, where only collectors/ and
    simulation/ may be copied across.  Keeping the loader local avoids a
    dependency on utils/config_loader.py from the host-side backend project.
    """
    if not config_path:
        config_path = "config.yaml"

    path = Path(config_path)
    if not path.exists():
        raise IOError("Config file not found: {0}".format(config_path))

    try:
        import yaml
    except Exception:
        yaml = None

    with open(str(path), "r") as handle:
        raw = handle.read()

    if yaml is not None:
        cfg = yaml.safe_load(raw)
    else:
        # JSON is valid YAML, so this gives a useful fallback for minimal VMs.
        cfg = json.loads(raw)

    if cfg is None:
        cfg = {}
    if "paths" not in cfg or cfg["paths"] is None:
        cfg["paths"] = {}
    cfg["paths"].setdefault("prtg_raw_dir", "data/raw")
    if "devices" not in cfg or cfg["devices"] is None:
        cfg["devices"] = []
    if "prtg" not in cfg or cfg["prtg"] is None:
        cfg["prtg"] = {}
    if "snmp_prtg" not in cfg or cfg["snmp_prtg"] is None:
        cfg["snmp_prtg"] = {}
    return cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [snmp-prtg] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

CSV_FIELDS = [
    "timestamp", "device_ip",
    "if_in_octets", "if_out_octets", "if_speed",
    "if_in_errors", "cpu_load_pct", "mem_used_pct",
]

# IF-MIB OIDs. Numeric OIDs avoid depending on local MIB names.
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"  # Mbps


class CounterSnapshot(object):
    """Small Python 3.5 replacement for a dataclass."""

    def __init__(self, timestamp, in_octets, out_octets, in_errors, counter_bits):
        self.timestamp = timestamp
        self.in_octets = in_octets
        self.out_octets = out_octets
        self.in_errors = in_errors
        self.counter_bits = counter_bits


class SnmpPollError(RuntimeError):
    pass


class RotatingCsvWriter(object):
    """Append-safe daily CSV writer independent of prtg_collector.py.

    This avoids importing collectors/prtg_collector.py, which may use newer
    Python syntax on some project versions.
    """

    def __init__(self, output_dir, prefix="prtg_raw"):
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = None
        self._current_path = None
        self._write_header = False

    def _path_for(self, dt):
        filename = "{0}_{1}.csv".format(self.prefix, dt.strftime("%Y-%m-%d"))
        return self.output_dir / filename

    def _ensure_current_file(self, ts):
        dt = datetime.fromtimestamp(ts, timezone.utc)
        date_key = dt.date()
        if date_key != self._current_date:
            self._current_date = date_key
            self._current_path = self._path_for(dt)
            self._write_header = (
                (not self._current_path.exists()) or self._current_path.stat().st_size == 0
            )
        return self._current_path

    def write_rows(self, rows):
        if not rows:
            return

        by_day = {}
        for row in rows:
            dt = datetime.fromtimestamp(row["timestamp"], timezone.utc)
            key = dt.strftime("%Y-%m-%d")
            by_day.setdefault(key, []).append(row)

        for _day, day_rows in by_day.items():
            path = self._ensure_current_file(day_rows[0]["timestamp"])
            with open(str(path), "a", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                if self._write_header:
                    writer.writeheader()
                    self._write_header = False
                for row in day_rows:
                    writer.writerow(row)


class SnmpClient(object):
    """Small SNMP GET wrapper.

    backends:
      auto   - try pysnmp, then CLI
      cli    - use net-snmp's snmpget command
      pysnmp - use pysnmp if installed
    """

    def __init__(self, community="public", port=161, timeout_sec=2.0, retries=1, backend="auto"):
        self.community = community
        self.port = port
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.backend = backend

    def get_int(self, host, oid):
        value = None
        errors = []

        if self.backend in ("auto", "pysnmp"):
            try:
                value = self._get_with_pysnmp(host, oid)
            except Exception as exc:
                errors.append("pysnmp={0}".format(exc))
                if self.backend == "pysnmp":
                    raise SnmpPollError("SNMP GET failed for {0} {1}: {2}".format(host, oid, exc))

        if value is None and self.backend in ("auto", "cli"):
            try:
                value = self._get_with_cli(host, oid)
            except Exception as exc:
                errors.append("cli={0}".format(exc))
                if self.backend == "cli":
                    raise SnmpPollError("SNMP GET failed for {0} {1}: {2}".format(host, oid, exc))

        if value is None:
            log.debug("SNMP GET returned no value for %s %s (%s)", host, oid, "; ".join(errors))
            return None

        return _parse_snmp_int(value)

    def _get_with_cli(self, host, oid):
        if not shutil.which("snmpget"):
            raise SnmpPollError("snmpget command not found; install snmp/net-snmp")

        cmd = [
            "snmpget",
            "-v2c",
            "-c", self.community,
            "-Oqv",
            "-t", str(self.timeout_sec),
            "-r", str(self.retries),
            "{0}:{1}".format(host, self.port),
            oid,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_sec * (self.retries + 2))
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise SnmpPollError("snmpget timed out")

        out_text = stdout.decode("utf-8", "replace").strip()
        err_text = stderr.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise SnmpPollError(err_text or out_text or "snmpget exited {0}".format(proc.returncode))
        return out_text

    def _get_with_pysnmp(self, host, oid):
        try:
            from pysnmp.hlapi import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                SnmpEngine,
                UdpTransportTarget,
                getCmd,
            )
        except Exception:
            raise SnmpPollError("pysnmp is not installed")

        iterator = getCmd(
            SnmpEngine(),
            CommunityData(self.community, mpModel=1),
            UdpTransportTarget((host, self.port), timeout=self.timeout_sec, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        error_indication, error_status, _error_index, var_binds = next(iterator)
        if error_indication:
            raise SnmpPollError(str(error_indication))
        if error_status:
            raise SnmpPollError(str(error_status.prettyPrint()))
        if not var_binds:
            return None
        return str(var_binds[0][1])


def _parse_snmp_int(raw):
    if raw is None:
        return None
    s = str(raw).strip().strip('"')
    if not s or "No Such" in s or "Timeout" in s:
        return None
    if ":" in s:
        s = s.split(":", 1)[1].strip().strip('"')
    token = s.split()[0].replace(",", "")
    try:
        return int(float(token))
    except (TypeError, ValueError):
        return None


def _counter_delta(current, previous, bits=64):
    if current >= previous:
        return current - previous
    max_value = 2 ** bits
    return (max_value - previous) + current


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def _stable_jitter(key, timestamp, amplitude=2.0):
    seed = sum(ord(ch) for ch in str(key)) % 360
    return math.sin((timestamp / 300.0) + seed) * amplitude


def _traffic_util_pct(in_bytes, out_bytes, elapsed_sec, speed_bps):
    if elapsed_sec <= 0 or speed_bps <= 0:
        return 0.0
    peak_bytes = max(in_bytes, out_bytes)
    return _clamp((peak_bytes * 8.0 / elapsed_sec) / speed_bps * 100.0, 0.0, 100.0)


def _synthetic_host_metrics(device_key, timestamp, util_pct, cfg, in_errors_delta=0):
    cfg = cfg or {}
    base_cpu = float(cfg.get("base_cpu_pct", 8.0))
    base_mem = float(cfg.get("base_mem_pct", 38.0))
    cpu_weight = float(cfg.get("cpu_util_weight", 0.75))
    mem_weight = float(cfg.get("mem_util_weight", 0.10))
    error_weight = float(cfg.get("cpu_error_weight", 0.5))
    jitter = float(cfg.get("jitter_pct", 2.0))

    cpu = base_cpu + (util_pct * cpu_weight) + (in_errors_delta * error_weight)
    mem = base_mem + (util_pct * mem_weight) + _stable_jitter(device_key, timestamp, jitter)
    return round(_clamp(cpu, 0.0, 100.0), 2), round(_clamp(mem, 0.0, 100.0), 2)


def _device_snmp_cfg(device):
    sensors = device.get("sensors") or {}
    snmp_cfg = sensors.get("snmp") or {}
    if not snmp_cfg and any(k in sensors for k in ("snmp_host", "host", "community", "if_index")):
        snmp_cfg = sensors
    return snmp_cfg


def _state_key(device, snmp_cfg):
    host = snmp_cfg.get("host") or snmp_cfg.get("snmp_host") or device.get("ip")
    if_index = int(snmp_cfg.get("if_index", 1))
    return "{0}|{1}|if{2}".format(device.get("ip"), host, if_index)


def _speed_from_snmp_or_config(client, host, if_index, snmp_cfg):
    configured = snmp_cfg.get("if_speed_bps") or snmp_cfg.get("if_speed")
    if configured:
        return float(configured)

    high_speed_mbps = client.get_int(host, "{0}.{1}".format(OID_IF_HIGH_SPEED, if_index))
    if high_speed_mbps and high_speed_mbps > 0:
        return float(high_speed_mbps * 1000000)

    speed = client.get_int(host, "{0}.{1}".format(OID_IF_SPEED, if_index))
    return float(speed or 0)


def read_snapshot(client, host, if_index, timestamp):
    """Read one interface counter snapshot. HC counters are preferred."""
    in_octets = client.get_int(host, "{0}.{1}".format(OID_IF_HC_IN_OCTETS, if_index))
    out_octets = client.get_int(host, "{0}.{1}".format(OID_IF_HC_OUT_OCTETS, if_index))
    counter_bits = 64

    if in_octets is None or out_octets is None:
        in_octets = client.get_int(host, "{0}.{1}".format(OID_IF_IN_OCTETS, if_index))
        out_octets = client.get_int(host, "{0}.{1}".format(OID_IF_OUT_OCTETS, if_index))
        counter_bits = 32

    if in_octets is None or out_octets is None:
        return None

    in_errors = client.get_int(host, "{0}.{1}".format(OID_IF_IN_ERRORS, if_index)) or 0
    return CounterSnapshot(
        timestamp=timestamp,
        in_octets=int(in_octets),
        out_octets=int(out_octets),
        in_errors=int(in_errors),
        counter_bits=counter_bits,
    )


def row_from_snapshots(device, snmp_cfg, current, previous, speed_bps, emit_first_sample=False):
    """Convert two cumulative SNMP snapshots into one PRTG-style row."""
    if previous is None:
        if not emit_first_sample:
            return None
        elapsed = float(snmp_cfg.get("assumed_interval_sec", 60))
        in_delta = 0
        out_delta = 0
        err_delta = 0
    else:
        elapsed = max(current.timestamp - previous.timestamp, 1.0)
        bits = current.counter_bits or previous.counter_bits or 64
        in_delta = _counter_delta(current.in_octets, previous.in_octets, bits)
        out_delta = _counter_delta(current.out_octets, previous.out_octets, bits)
        err_delta = _counter_delta(current.in_errors, previous.in_errors, 32)

    util_pct = _traffic_util_pct(in_delta, out_delta, elapsed, speed_bps)

    if "cpu_load_pct" in snmp_cfg and "mem_used_pct" in snmp_cfg:
        cpu, mem = float(snmp_cfg["cpu_load_pct"]), float(snmp_cfg["mem_used_pct"])
    else:
        cpu, mem = _synthetic_host_metrics(
            device.get("ip", snmp_cfg.get("host", "device")),
            current.timestamp,
            util_pct,
            snmp_cfg.get("synthetic", {}),
            err_delta,
        )

    return {
        "timestamp": float(current.timestamp),
        "device_ip": snmp_cfg.get("output_ip") or device.get("ip"),
        "if_in_octets": float(in_delta),
        "if_out_octets": float(out_delta),
        "if_speed": float(speed_bps),
        "if_in_errors": float(err_delta),
        "cpu_load_pct": float(cpu),
        "mem_used_pct": float(mem),
    }


def poll_snmp_device(client, device, previous_state, now_ts=None, emit_first_sample=False):
    snmp_cfg = _device_snmp_cfg(device)
    if not snmp_cfg or snmp_cfg.get("enabled", True) is False:
        return None

    host = snmp_cfg.get("host") or snmp_cfg.get("snmp_host") or device.get("ip")
    if not host:
        log.warning("Skipping device without SNMP host: %s", device)
        return None

    if_index = int(snmp_cfg.get("if_index", 1))
    timestamp = float(now_ts or time.time())

    snapshot = read_snapshot(client, host, if_index, timestamp)
    if snapshot is None:
        log.warning("No SNMP counters returned for %s (%s ifIndex=%s)", device.get("name"), host, if_index)
        return None

    key = _state_key(device, snmp_cfg)
    prev_raw = previous_state.get(key)
    previous = None
    if prev_raw:
        previous = CounterSnapshot(
            timestamp=float(prev_raw["timestamp"]),
            in_octets=int(prev_raw["in_octets"]),
            out_octets=int(prev_raw["out_octets"]),
            in_errors=int(prev_raw.get("in_errors", 0)),
            counter_bits=int(prev_raw.get("counter_bits", snapshot.counter_bits)),
        )

    speed_bps = _speed_from_snmp_or_config(client, host, if_index, snmp_cfg)
    row = row_from_snapshots(device, snmp_cfg, snapshot, previous, speed_bps, emit_first_sample)

    previous_state[key] = {
        "timestamp": snapshot.timestamp,
        "in_octets": snapshot.in_octets,
        "out_octets": snapshot.out_octets,
        "in_errors": snapshot.in_errors,
        "counter_bits": snapshot.counter_bits,
    }
    return row


def load_state(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(str(path), "r") as handle:
            return json.load(handle)
    except Exception:
        log.warning("Could not read SNMP state file %s; starting fresh", path)
        return {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(str(tmp), "w") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    if str(tmp) != str(path):
        try:
            tmp.replace(path)
        except AttributeError:
            os.rename(str(tmp), str(path))


def _snmp_devices(cfg):
    devices = []
    for dev in cfg.get("devices", []):
        if _device_snmp_cfg(dev).get("enabled", True):
            devices.append(dev)
    return devices


def run_poll(cfg, output_dir, state_file, backend="auto", once=False,
             emit_first_sample=False, interval_override=None):
    collector_cfg = cfg.get("snmp_prtg", {})
    prtg_cfg = cfg.get("prtg", {})
    interval = int(interval_override or collector_cfg.get("poll_interval_sec") or prtg_cfg.get("poll_interval_sec", 60))
    timeout = float(collector_cfg.get("timeout_sec", 2.0))
    retries = int(collector_cfg.get("retries", 1))

    devices = _snmp_devices(cfg)
    if not devices:
        log.warning("No SNMP-enabled devices found in config.yaml")

    writer = RotatingCsvWriter(output_dir, prefix="prtg_raw")
    state = load_state(state_file)

    log.info(
        "Starting SNMP-backed PRTG collector: %d devices, every %ss, backend=%s, output=%s",
        len(devices), interval, backend, output_dir,
    )

    total_rows = 0
    try:
        while True:
            rows = []
            now_ts = time.time()
            for device in devices:
                snmp_cfg = _device_snmp_cfg(device)
                client = SnmpClient(
                    community=str(snmp_cfg.get("community", collector_cfg.get("community", "public"))),
                    port=int(snmp_cfg.get("port", collector_cfg.get("port", 161))),
                    timeout_sec=float(snmp_cfg.get("timeout_sec", timeout)),
                    retries=int(snmp_cfg.get("retries", retries)),
                    backend=backend,
                )
                try:
                    row = poll_snmp_device(client, device, state, now_ts, emit_first_sample)
                    if row:
                        rows.append(row)
                except Exception:
                    log.exception("Failed polling SNMP device %s (%s)", device.get("name"), device.get("ip"))

            save_state(state_file, state)

            if rows:
                writer.write_rows(rows)
                total_rows += len(rows)
                log.info("Wrote %d rows this cycle (total=%d)", len(rows), total_rows)
            else:
                log.info("No rows written this cycle (first cycle usually initializes counters)")

            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        save_state(state_file, state)
        log.info("Shutting down. Total rows collected: %d", total_rows)


def main():
    parser = argparse.ArgumentParser(description="SNMP-backed PRTG-style collector for Mininet simulations")
    parser.add_argument("--mode", choices=["poll", "once"], default="poll")
    parser.add_argument("--config", default=None, help="Path to config.yaml or simulation/mininet_config.generated.yaml")
    parser.add_argument("--output-dir", default=None, help="Override PRTG raw output directory")
    parser.add_argument("--state-file", default=None, help="Counter state JSON path")
    parser.add_argument("--backend", choices=["auto", "cli", "pysnmp"], default="auto")
    parser.add_argument("--interval", type=int, default=None, help="Override polling interval seconds")
    parser.add_argument("--emit-first-sample", action="store_true", help="Write zero-delta rows on first poll")
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_dir = Path(args.output_dir) if args.output_dir else Path(cfg.get("paths", {}).get("prtg_raw_dir", "data/raw"))
    state_file = Path(args.state_file) if args.state_file else output_dir / "snmp_prtg_state.json"

    run_poll(
        cfg,
        output_dir=output_dir,
        state_file=state_file,
        backend=args.backend,
        once=(args.mode == "once"),
        emit_first_sample=args.emit_first_sample,
        interval_override=args.interval,
    )


if __name__ == "__main__":
    main()
