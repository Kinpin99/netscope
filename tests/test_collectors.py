"""
test_collectors.py
-------------------
Covers the fixes applied to packet_utils.py and netflow_collector.py:

  - NetFlow v9 template cache is keyed by (source_addr, template_id), so
    two exporters reusing the same template_id with different field
    layouts don't corrupt each other's parsing (Issue #8)
  - RotatingCsvWriter creates one file per UTC day and writes headers
    correctly for each (Issue #7)
"""

import shutil
import struct
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from collectors.packet_utils import (
    _NF9_TEMPLATES,
    _decode_nf9_data,
    _parse_nf9_template,
    NetFlowRecord,
)
from collectors.netflow_collector import RotatingCsvWriter, CSV_FIELDS


def _build_template_flowset(tmpl_id, fields):
    payload = struct.pack("!HH", tmpl_id, len(fields))
    for ftype, flen in fields:
        payload += struct.pack("!HH", ftype, flen)
    return payload


@pytest.fixture(autouse=True)
def clear_template_cache():
    """Each test gets a clean module-level template cache."""
    _NF9_TEMPLATES.clear()
    yield
    _NF9_TEMPLATES.clear()


class TestNF9MultiExporter:
    def test_same_template_id_different_layouts(self):
        """
        Router A's template 256 = [IN_BYTES(4), IPV4_SRC_ADDR(4)]
        Router B's template 256 = [PROTOCOL(1), L4_DST_PORT(2)]
        Both must be cached and decoded independently.
        """
        router_a_tmpl = _build_template_flowset(256, [(1, 4), (8, 4)])
        router_b_tmpl = _build_template_flowset(256, [(4, 1), (11, 2)])

        _parse_nf9_template(router_a_tmpl, source_addr="192.168.1.1")
        _parse_nf9_template(router_b_tmpl, source_addr="192.168.1.2")

        assert ("192.168.1.1", 256) in _NF9_TEMPLATES
        assert ("192.168.1.2", 256) in _NF9_TEMPLATES
        assert _NF9_TEMPLATES[("192.168.1.1", 256)] != _NF9_TEMPLATES[("192.168.1.2", 256)]

    def test_decode_uses_correct_per_exporter_template(self):
        router_a_tmpl = _build_template_flowset(256, [(1, 4), (8, 4)])  # IN_BYTES, IPV4_SRC_ADDR
        router_b_tmpl = _build_template_flowset(256, [(4, 1), (11, 2)])  # PROTOCOL, L4_DST_PORT

        _parse_nf9_template(router_a_tmpl, source_addr="192.168.1.1")
        _parse_nf9_template(router_b_tmpl, source_addr="192.168.1.2")

        data_a = struct.pack("!I", 5000) + socket.inet_aton("10.0.0.5")
        recs_a = _decode_nf9_data(256, data_a, recv_time=1.0, source_addr="192.168.1.1")
        assert len(recs_a) == 1
        assert recs_a[0].bytes_ == 5000
        assert recs_a[0].src_ip == "10.0.0.5"

        data_b = struct.pack("!B", 6) + struct.pack("!H", 443)
        recs_b = _decode_nf9_data(256, data_b, recv_time=1.0, source_addr="192.168.1.2")
        assert len(recs_b) == 1
        assert recs_b[0].protocol == 6
        assert recs_b[0].dst_port == 443

    def test_unknown_template_returns_empty(self):
        """Data arriving before its template (or for an unseen exporter) is dropped, not crashed on."""
        recs = _decode_nf9_data(999, b"\x00\x00\x00\x00", recv_time=1.0, source_addr="10.10.10.10")
        assert recs == []

    def test_default_source_addr_for_single_exporter_callers(self):
        """pcap/legacy callers that don't pass source_addr still work via 'default' key."""
        tmpl = _build_template_flowset(300, [(1, 4)])
        _parse_nf9_template(tmpl)  # no source_addr -> "default"
        assert ("default", 300) in _NF9_TEMPLATES

        data = struct.pack("!I", 1234)
        recs = _decode_nf9_data(300, data, recv_time=1.0)
        assert recs[0].bytes_ == 1234


class TestRotatingCsvWriter:
    @pytest.fixture
    def tmp_output_dir(self, tmp_path):
        return tmp_path / "raw"

    def test_creates_daily_files(self, tmp_output_dir):
        writer = RotatingCsvWriter(tmp_output_dir, prefix="netflow_raw")
        day1 = 1718000000
        day2 = day1 + 86400

        rec1 = NetFlowRecord("10.0.0.5", "8.8.8.8", 1, 80, 6, 0, 1, 100, 0, 0, day1)
        rec2 = NetFlowRecord("10.0.0.6", "1.1.1.1", 1, 443, 6, 0, 1, 200, 0, 0, day2)

        writer.write_records([rec1])
        writer.write_records([rec2])

        files = sorted(tmp_output_dir.glob("netflow_raw_*.csv"))
        assert len(files) == 2

    def test_header_written_once_per_file(self, tmp_output_dir):
        writer = RotatingCsvWriter(tmp_output_dir, prefix="netflow_raw")
        ts = 1718000000
        rec = NetFlowRecord("10.0.0.5", "8.8.8.8", 1, 80, 6, 0, 1, 100, 0, 0, ts)

        writer.write_records([rec])
        writer.write_records([rec])

        files = list(tmp_output_dir.glob("netflow_raw_*.csv"))
        content = files[0].read_text()
        header_line = ",".join(CSV_FIELDS)
        assert content.count(header_line) == 1
        assert content.count("10.0.0.5") == 2

    def test_existing_file_does_not_get_duplicate_header(self, tmp_output_dir):
        """If a file from a previous run already exists with a header,
        a fresh writer instance should not write the header again."""
        tmp_output_dir.mkdir(parents=True)
        ts = 1718000000
        from datetime import datetime, timezone
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        existing = tmp_output_dir / f"netflow_raw_{date_str}.csv"
        existing.write_text(",".join(CSV_FIELDS) + "\n")

        writer = RotatingCsvWriter(tmp_output_dir, prefix="netflow_raw")
        rec = NetFlowRecord("10.0.0.5", "8.8.8.8", 1, 80, 6, 0, 1, 100, 0, 0, ts)
        writer.write_records([rec])

        content = existing.read_text()
        header_line = ",".join(CSV_FIELDS)
        assert content.count(header_line) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
