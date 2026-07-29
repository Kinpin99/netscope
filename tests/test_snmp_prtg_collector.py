"""
test_snmp_prtg_collector.py
----------------------------
Covers the Mininet simulation collector that converts SNMP counters into the
same PRTG-style CSV schema used by the existing preprocessing pipeline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.prtg_collector import CSV_FIELDS, RotatingCsvWriter
from collectors.snmp_prtg_collector import (
    OID_IF_HC_IN_OCTETS,
    OID_IF_HC_OUT_OCTETS,
    OID_IF_HIGH_SPEED,
    OID_IF_IN_ERRORS,
    CounterSnapshot,
    _counter_delta,
    _parse_snmp_int,
    poll_snmp_device,
    row_from_snapshots,
)
from preprocessing.unified_preprocessing import _load_snmp


class FakeSnmpClient:
    def __init__(self, values):
        self.values = values

    def get_int(self, host, oid):
        return self.values.get((host, oid))


class TestParseSnmpInt:
    def test_plain_integer(self):
        assert _parse_snmp_int("12345") == 12345

    def test_typed_counter(self):
        assert _parse_snmp_int("Counter64: 1,234") == 1234

    def test_quoted_value(self):
        assert _parse_snmp_int('"42"') == 42

    def test_no_such_returns_none(self):
        assert _parse_snmp_int("No Such Object available on this agent at this OID") is None


class TestCounterDelta:
    def test_normal_delta(self):
        assert _counter_delta(150, 100, 64) == 50

    def test_wrap_32_bit(self):
        assert _counter_delta(10, (2**32) - 5, 32) == 15


class TestRowFromSnapshots:
    def test_converts_cumulative_counters_to_deltas(self):
        device = {"ip": "10.0.3.10", "name": "web-server"}
        cfg = {"output_ip": "10.0.3.10", "if_speed_bps": 100_000_000}
        prev = CounterSnapshot(1000, 100_000, 50_000, 0, 64)
        curr = CounterSnapshot(1060, 220_000, 80_000, 1, 64)
        row = row_from_snapshots(device, cfg, curr, prev, 100_000_000)

        assert row["device_ip"] == "10.0.3.10"
        assert row["if_in_octets"] == 120_000.0
        assert row["if_out_octets"] == 30_000.0
        assert row["if_in_errors"] == 1.0
        assert 0 <= row["cpu_load_pct"] <= 100
        assert 0 <= row["mem_used_pct"] <= 100
        assert list(row.keys()) == CSV_FIELDS

    def test_first_sample_skipped_by_default(self):
        device = {"ip": "10.0.3.10"}
        curr = CounterSnapshot(1060, 220_000, 80_000, 0, 64)
        assert row_from_snapshots(device, {}, curr, None, 100_000_000) is None


class TestPollSnmpDevice:
    def test_polls_hc_counters_and_uses_state(self):
        device = {
            "ip": "10.0.3.10",
            "name": "web-server",
            "sensors": {
                "snmp": {
                    "enabled": True,
                    "host": "10.255.0.10",
                    "community": "public",
                    "if_index": 7,
                    "output_ip": "10.0.3.10",
                }
            },
        }
        host = "10.255.0.10"
        idx = 7
        client1 = FakeSnmpClient({
            (host, f"{OID_IF_HC_IN_OCTETS}.{idx}"): 100_000,
            (host, f"{OID_IF_HC_OUT_OCTETS}.{idx}"): 50_000,
            (host, f"{OID_IF_IN_ERRORS}.{idx}"): 0,
            (host, f"{OID_IF_HIGH_SPEED}.{idx}"): 100,
        })
        state = {}
        assert poll_snmp_device(client1, device, state, now_ts=1000) is None

        client2 = FakeSnmpClient({
            (host, f"{OID_IF_HC_IN_OCTETS}.{idx}"): 160_000,
            (host, f"{OID_IF_HC_OUT_OCTETS}.{idx}"): 80_000,
            (host, f"{OID_IF_IN_ERRORS}.{idx}"): 2,
            (host, f"{OID_IF_HIGH_SPEED}.{idx}"): 100,
        })
        row = poll_snmp_device(client2, device, state, now_ts=1060)

        assert row["device_ip"] == "10.0.3.10"
        assert row["if_in_octets"] == 60_000.0
        assert row["if_out_octets"] == 30_000.0
        assert row["if_in_errors"] == 2.0
        assert row["if_speed"] == 100_000_000.0


class TestOutputContract:
    def test_written_rows_load_with_existing_snmp_loader(self, tmp_path):
        writer = RotatingCsvWriter(tmp_path)
        writer.write_rows([
            {
                "timestamp": 1781359200.0,
                "device_ip": "10.0.3.10",
                "if_in_octets": 1000.0,
                "if_out_octets": 2000.0,
                "if_speed": 100_000_000.0,
                "if_in_errors": 0.0,
                "cpu_load_pct": 10.0,
                "mem_used_pct": 40.0,
            }
        ])
        loaded = _load_snmp(str(tmp_path))
        assert len(loaded) == 1
        assert list(loaded.columns) == CSV_FIELDS
        assert loaded.iloc[0]["device_ip"] == "10.0.3.10"
