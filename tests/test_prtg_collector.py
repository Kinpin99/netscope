"""
test_prtg_collector.py
-----------------------
Covers:
  - PRTG datetime parsing (_to_epoch)
  - Channel value extraction with candidate fallback names
  - poll_device merging multiple sensors into one row per timestamp
  - RotatingCsvWriter daily rotation
  - Schema contract: prtg_collector output is directly loadable by
    unified_preprocessing._load_snmp() (Issue #1 from the design review)
"""

import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from collectors.prtg_collector import (
    CSV_FIELDS,
    PrtgClient,
    RotatingCsvWriter,
    _extract_channel_value,
    _to_epoch,
    poll_device,
    CHANNEL_CANDIDATES,
)
from preprocessing.unified_preprocessing import _load_snmp


# ---------------------------------------------------------------------------
# _to_epoch
# ---------------------------------------------------------------------------
class TestToEpoch:
    def test_standard_prtg_format(self):
        ts = _to_epoch("06/13/2026 14:00:00")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.year == 2026 and dt.month == 6 and dt.day == 13
        assert dt.hour == 14

    def test_iso_format(self):
        ts = _to_epoch("2026-06-13 14:00:00")
        assert ts is not None

    def test_unparseable_returns_none(self):
        assert _to_epoch("not a date") is None


# ---------------------------------------------------------------------------
# Channel extraction
# ---------------------------------------------------------------------------
class TestExtractChannelValue:
    def test_first_candidate_match(self):
        point = {"Traffic In_raw": 12345.0}
        val = _extract_channel_value(point, CHANNEL_CANDIDATES["if_in_octets"])
        assert val == 12345.0

    def test_fallback_to_second_candidate(self):
        point = {"In_raw": 999.0}
        val = _extract_channel_value(point, CHANNEL_CANDIDATES["if_in_octets"])
        assert val == 999.0

    def test_no_match_returns_none(self):
        point = {"SomethingElse_raw": 1.0}
        val = _extract_channel_value(point, CHANNEL_CANDIDATES["if_in_octets"])
        assert val is None

    def test_non_numeric_value_skipped(self):
        point = {"Traffic In_raw": "not-a-number", "In_raw": 50.0}
        val = _extract_channel_value(point, CHANNEL_CANDIDATES["if_in_octets"])
        assert val == 50.0


# ---------------------------------------------------------------------------
# poll_device
# ---------------------------------------------------------------------------
class TestPollDevice:
    @pytest.fixture
    def device(self):
        return {
            "ip": "10.0.0.1",
            "name": "core-router-01",
            "sensors": {
                "traffic_in": 1001,
                "traffic_out": 1002,
                "if_speed_bps": 1_000_000_000,
                "if_errors": 1003,
                "cpu": 1004,
                "memory": 1005,
            },
        }

    def _fake_historic_data(self, sensor_id, start, end, avg_interval_sec=60):
        base_dt = "06/13/2026 14:00:00"
        return {
            1001: [{"datetime": base_dt, "Traffic In_raw": 1500000.0}],
            1002: [{"datetime": base_dt, "Traffic Out_raw": 800000.0}],
            1003: [{"datetime": base_dt, "Errors In_raw": 2.0}],
            1004: [{"datetime": base_dt, "Total_raw": 23.5}],
            1005: [{"datetime": base_dt, "Memory Usage_raw": 47.2}],
        }.get(sensor_id, [])

    def test_merges_all_sensors_into_one_row(self, device):
        with patch.object(PrtgClient, "historic_data", self._fake_historic_data):
            client = PrtgClient("https://prtg.example.local", "fake-token")
            now = datetime.now(timezone.utc)
            rows = poll_device(client, device, now - timedelta(minutes=1), now, 60)

        assert len(rows) == 1
        row = rows[0]
        assert row["device_ip"] == "10.0.0.1"
        assert row["if_in_octets"] == 1500000.0
        assert row["if_out_octets"] == 800000.0
        assert row["if_speed"] == 1_000_000_000
        assert row["if_in_errors"] == 2.0
        assert row["cpu_load_pct"] == 23.5
        assert row["mem_used_pct"] == 47.2

    def test_missing_sensor_defaults_to_zero(self, device):
        """A device without a 'memory' sensor configured should still produce
        a row, with mem_used_pct defaulting to 0."""
        device["sensors"].pop("memory")

        def fake_hd(sensor_id, start, end, avg_interval_sec=60):
            base_dt = "06/13/2026 14:00:00"
            return {
                1001: [{"datetime": base_dt, "Traffic In_raw": 1500000.0}],
                1002: [{"datetime": base_dt, "Traffic Out_raw": 800000.0}],
                1003: [{"datetime": base_dt, "Errors In_raw": 0.0}],
                1004: [{"datetime": base_dt, "Total_raw": 10.0}],
            }.get(sensor_id, [])

        with patch.object(PrtgClient, "historic_data", lambda self, sid, s, e, avg_interval_sec=60: fake_hd(sid, s, e, avg_interval_sec)):
            client = PrtgClient("https://prtg.example.local", "fake-token")
            now = datetime.now(timezone.utc)
            rows = poll_device(client, device, now - timedelta(minutes=1), now, 60)

        assert len(rows) == 1
        assert rows[0]["mem_used_pct"] == 0.0
        assert rows[0]["cpu_load_pct"] == 10.0

    def test_no_data_returns_empty(self, device):
        with patch.object(PrtgClient, "historic_data", lambda self, sid, s, e, avg_interval_sec=60: []):
            client = PrtgClient("https://prtg.example.local", "fake-token")
            now = datetime.now(timezone.utc)
            rows = poll_device(client, device, now - timedelta(minutes=1), now, 60)
        assert rows == []


# ---------------------------------------------------------------------------
# RotatingCsvWriter + schema contract with _load_snmp
# ---------------------------------------------------------------------------
class TestOutputContract:
    @pytest.fixture
    def out_dir(self, tmp_path):
        return tmp_path / "prtg_raw"

    def test_columns_match_csv_fields(self, out_dir):
        writer = RotatingCsvWriter(out_dir)
        row = {f: 0 for f in CSV_FIELDS}
        row["timestamp"] = 1781359200.0
        row["device_ip"] = "10.0.0.1"
        writer.write_rows([row])

        files = list(out_dir.glob("prtg_raw_*.csv"))
        assert len(files) == 1
        header = files[0].read_text().splitlines()[0]
        assert header.split(",") == CSV_FIELDS

    def test_output_loadable_by_load_snmp(self, out_dir):
        writer = RotatingCsvWriter(out_dir)
        rows = [
            {"timestamp": 1781359200.0, "device_ip": "10.0.0.1",
             "if_in_octets": 1500000.0, "if_out_octets": 800000.0,
             "if_speed": 1e9, "if_in_errors": 2.0,
             "cpu_load_pct": 23.5, "mem_used_pct": 47.2},
            {"timestamp": 1781359260.0, "device_ip": "10.0.0.1",
             "if_in_octets": 1600000.0, "if_out_octets": 850000.0,
             "if_speed": 1e9, "if_in_errors": 2.0,
             "cpu_load_pct": 25.0, "mem_used_pct": 48.0},
        ]
        writer.write_rows(rows)

        loaded = _load_snmp(str(out_dir))
        assert len(loaded) == 2
        assert list(loaded.columns) == CSV_FIELDS
        assert set(loaded["device_ip"]) == {"10.0.0.1"}

    def test_multi_day_rows_split_into_separate_files(self, out_dir):
        writer = RotatingCsvWriter(out_dir)
        day1 = 1781359200.0
        day2 = day1 + 86400
        rows = [
            {f: 0 for f in CSV_FIELDS} | {"timestamp": day1, "device_ip": "10.0.0.1"},
            {f: 0 for f in CSV_FIELDS} | {"timestamp": day2, "device_ip": "10.0.0.1"},
        ]
        writer.write_rows(rows)

        files = sorted(out_dir.glob("prtg_raw_*.csv"))
        assert len(files) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
