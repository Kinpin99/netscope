"""
test_preprocessing.py
----------------------
Covers the fixes applied to unified_preprocessing.py:

  - _assign_device_ip correctly attributes inbound-from-external traffic
    to the internal device (Issue #3)
  - DeviceBehaviorFeatures / ProtocolFeatures use _assign_device_ip, not
    raw src_ip (Issue #3)
  - from_stream produces zero z-scores with no normalization_stats, and
    meaningful z-scores when stats are supplied (Issue #5)
  - _load_netflow / _load_snmp support directory-of-daily-files input
    (Issue #7 consequence)
  - _load_baseline handles a missing baseline file gracefully (Issue #6)
  - PortScanFeatures no longer requires/accepts an snmp argument (Issue #2)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from preprocessing.unified_preprocessing import (
    BandwidthFeatures,
    DeviceBehaviorFeatures,
    PortScanFeatures,
    ProtocolFeatures,
    _assign_device_ip,
    _is_private_ip,
    _load_baseline,
    _load_netflow,
    _load_snmp,
    build_all_normalization_stats,
    build_all_features,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_ts():
    return 1718000000


@pytest.fixture
def inbound_attack_df(base_ts):
    """
    A flow from an external attacker TO an internal device.
    Before the fix, device_ip = src_ip would attribute this to the
    external attacker's IP, not the internal device being attacked.
    """
    return pd.DataFrame([
        {
            "timestamp": base_ts + 1, "src_ip": "203.0.113.50", "dst_ip": "10.0.0.5",
            "src_port": 54321, "dst_port": 22, "protocol": 6, "tcp_flags": 0x02,
            "packets": 1, "bytes": 60, "duration_sec": 0.01,
        },
        {
            "timestamp": base_ts + 2, "src_ip": "203.0.113.50", "dst_ip": "10.0.0.5",
            "src_port": 54321, "dst_port": 23, "protocol": 6, "tcp_flags": 0x02,
            "packets": 1, "bytes": 60, "duration_sec": 0.01,
        },
    ])


@pytest.fixture
def outbound_df(base_ts):
    """A normal outbound flow from an internal device to an external host."""
    return pd.DataFrame([
        {
            "timestamp": base_ts + 1, "src_ip": "10.0.0.6", "dst_ip": "8.8.8.8",
            "src_port": 51000, "dst_port": 443, "protocol": 6, "tcp_flags": 0x10,
            "packets": 10, "bytes": 1500, "duration_sec": 1.0,
        },
    ])


@pytest.fixture
def empty_snmp():
    return pd.DataFrame(columns=[
        "timestamp", "device_ip", "if_in_octets", "if_out_octets",
        "if_speed", "if_in_errors", "cpu_load_pct", "mem_used_pct",
    ])


# ---------------------------------------------------------------------------
# Issue #3: device_ip assignment
# ---------------------------------------------------------------------------

class TestAssignDeviceIp:
    def test_inbound_external_attributed_to_internal_dst(self, inbound_attack_df):
        result = _assign_device_ip(inbound_attack_df)
        assert (result["device_ip"] == "10.0.0.5").all(), (
            "Inbound flow from an external attacker must be attributed to "
            "the internal destination device, not the external source"
        )

    def test_outbound_attributed_to_internal_src(self, outbound_df):
        result = _assign_device_ip(outbound_df)
        assert (result["device_ip"] == "10.0.0.6").all()

    def test_device_behavior_sees_attacker_traffic(self, inbound_attack_df, empty_snmp):
        feat = DeviceBehaviorFeatures.from_stream(inbound_attack_df, empty_snmp)
        assert "10.0.0.5" in feat["device_ip"].values, (
            "DeviceBehaviorFeatures must profile the internal device "
            "10.0.0.5 even though it never appears as src_ip"
        )
        row = feat[feat["device_ip"] == "10.0.0.5"].iloc[0]
        assert row["bytes_in"] == 120  # 60 + 60 from the two attack packets

    def test_protocol_features_sees_attacker_traffic(self, inbound_attack_df):
        feat = ProtocolFeatures.from_stream(inbound_attack_df)
        assert "10.0.0.5" in feat["device_ip"].values, (
            "ProtocolFeatures must profile the internal device 10.0.0.5"
        )


# ---------------------------------------------------------------------------
# Issue #5: live z-score architecture
# ---------------------------------------------------------------------------

class TestLiveZScores:
    def test_zscore_zero_without_stats(self, outbound_df, empty_snmp):
        feat = BandwidthFeatures.from_stream(outbound_df, empty_snmp)
        assert (feat["bw_in_zscore"] == 0).all()
        assert (feat["bw_out_zscore"] == 0).all()

    def test_zscore_nonzero_with_stats(self, outbound_df, empty_snmp):
        stats = {
            "bandwidth": {
                "10.0.0.6": {
                    "bw_out_bytes_mean": 100.0,
                    "bw_out_bytes_std": 10.0,
                    "bw_in_bytes_mean": 0.0,
                    "bw_in_bytes_std": 1.0,
                }
            }
        }
        feat = BandwidthFeatures.from_stream(outbound_df, empty_snmp, normalization_stats=stats)
        row = feat[feat["device_ip"] == "10.0.0.6"].iloc[0]
        # bw_out_bytes = 1500, mean=100, std=10 -> z = (1500-100)/10 = 140
        assert row["bw_out_zscore"] == pytest.approx(140.0)

    def test_unknown_device_defaults_to_zero(self, outbound_df, empty_snmp):
        """A device with no entry in normalization_stats gets z=0, same as
        a brand-new device during training (min_periods fallback)."""
        stats = {"bandwidth": {"10.0.0.99": {"bw_out_bytes_mean": 5, "bw_out_bytes_std": 1}}}
        feat = BandwidthFeatures.from_stream(outbound_df, empty_snmp, normalization_stats=stats)
        row = feat[feat["device_ip"] == "10.0.0.6"].iloc[0]
        assert row["bw_out_zscore"] == 0.0

    def test_device_behavior_stream_zscores(self, outbound_df, empty_snmp):
        feat = DeviceBehaviorFeatures.from_stream(outbound_df, empty_snmp)
        assert (feat["bytes_in_zscore"] == 0).all()
        assert (feat["cpu_util_zscore"] == 0).all()


# ---------------------------------------------------------------------------
# Issue #7 consequence: directory-of-daily-files loading
# ---------------------------------------------------------------------------

class TestDirectoryLoading:
    def test_load_netflow_from_directory(self, tmp_path, base_ts):
        df1 = pd.DataFrame([{
            "timestamp": base_ts, "src_ip": "10.0.0.5", "dst_ip": "8.8.8.8",
            "src_port": 1, "dst_port": 80, "protocol": 6, "tcp_flags": 0,
            "packets": 1, "bytes": 100, "duration_sec": 1.0,
        }])
        df2 = pd.DataFrame([{
            "timestamp": base_ts + 86400, "src_ip": "10.0.0.6", "dst_ip": "8.8.8.8",
            "src_port": 1, "dst_port": 80, "protocol": 6, "tcp_flags": 0,
            "packets": 1, "bytes": 200, "duration_sec": 1.0,
        }])
        df1.to_csv(tmp_path / "netflow_raw_2026-06-13.csv", index=False)
        df2.to_csv(tmp_path / "netflow_raw_2026-06-14.csv", index=False)

        result = _load_netflow(str(tmp_path))
        assert len(result) == 2
        assert set(result["src_ip"]) == {"10.0.0.5", "10.0.0.6"}

    def test_load_netflow_empty_directory(self, tmp_path):
        result = _load_netflow(str(tmp_path))
        assert result.empty
        assert "timestamp" in result.columns

    def test_load_snmp_missing_path_returns_empty(self, tmp_path):
        result = _load_snmp(str(tmp_path / "does_not_exist.csv"))
        assert result.empty
        assert "device_ip" in result.columns


# ---------------------------------------------------------------------------
# Issue #6: missing baseline file
# ---------------------------------------------------------------------------

class TestMissingBaseline:
    def test_load_baseline_missing_file(self, tmp_path):
        result = _load_baseline(str(tmp_path / "protocol_baseline.csv"))
        assert result == {}

    def test_protocol_features_with_missing_baseline(self, tmp_path, outbound_df):
        nf_path = tmp_path / "netflow.csv"
        outbound_df.to_csv(nf_path, index=False)
        # Should not raise even though baseline file doesn't exist
        feat = ProtocolFeatures.from_csv(str(nf_path), baseline_csv=str(tmp_path / "missing_baseline.csv"))
        assert (feat["kl_div_from_baseline"] == 0).all()
        assert (feat["num_new_protocols"] == 0).all()


# ---------------------------------------------------------------------------
# Issue #2: PortScanFeatures signature cleanup
# ---------------------------------------------------------------------------

class TestPortScanSignature:
    def test_from_stream_no_snmp_arg(self, outbound_df):
        # Should work with just the netflow dataframe
        feat = PortScanFeatures.from_stream(outbound_df)
        assert "src_ip" in feat.columns

    def test_from_csv_signature(self, tmp_path, outbound_df):
        nf_path = tmp_path / "netflow.csv"
        outbound_df.to_csv(nf_path, index=False)
        feat = PortScanFeatures.from_csv(str(nf_path))
        assert "src_ip" in feat.columns


# ---------------------------------------------------------------------------
# Integration: build_all_features + normalization stats round trip
# ---------------------------------------------------------------------------

class TestEmptyStreamInput:
    """
    Regression tests: live inference can call from_stream() with an empty
    netflow_df (e.g. a window with PRTG data but no flows, or vice versa).
    Previously, boolean-indexing with an empty Series produced by
    .apply(_is_private_ip) (dtype=object on empty input) silently dropped
    ALL columns from the filtered DataFrame, causing a KeyError deeper in
    groupby(["device_ip", ...]).
    """

    @pytest.fixture
    def empty_netflow(self):
        return pd.DataFrame(columns=[
            "timestamp", "src_ip", "dst_ip", "src_port", "dst_port",
            "protocol", "tcp_flags", "packets", "bytes", "duration_sec",
        ])

    def test_bandwidth_empty_input(self, empty_netflow, empty_snmp):
        feat = BandwidthFeatures.from_stream(empty_netflow, empty_snmp)
        assert feat.empty
        assert "device_ip" in feat.columns

    def test_portscan_empty_input(self, empty_netflow):
        feat = PortScanFeatures.from_stream(empty_netflow)
        assert feat.empty
        assert "src_ip" in feat.columns

    def test_device_behavior_empty_input(self, empty_netflow, empty_snmp):
        feat = DeviceBehaviorFeatures.from_stream(empty_netflow, empty_snmp)
        assert feat.empty
        assert "device_ip" in feat.columns

    def test_protocol_empty_input(self, empty_netflow):
        feat = ProtocolFeatures.from_stream(empty_netflow)
        assert feat.empty
        assert "device_ip" in feat.columns

    def test_assign_device_ip_empty_input(self, empty_netflow):
        result = _assign_device_ip(empty_netflow)
        assert "device_ip" in result.columns
        assert result.empty


class TestBuildAllFeatures:
    def test_build_all_features_and_stats(self, tmp_path, outbound_df, inbound_attack_df, empty_snmp):
        nf = pd.concat([outbound_df, inbound_attack_df], ignore_index=True)
        nf_path = tmp_path / "netflow.csv"
        snmp_path = tmp_path / "snmp.csv"
        nf.to_csv(nf_path, index=False)
        empty_snmp.to_csv(snmp_path, index=False)

        features = build_all_features(str(nf_path), str(snmp_path))
        assert set(features.keys()) == {"bandwidth", "portscan", "device_behavior", "protocol"}

        stats = build_all_normalization_stats(features)
        assert "bandwidth" in stats
        assert "device_behavior" in stats


# ---------------------------------------------------------------------------
# _is_private_ip sanity
# ---------------------------------------------------------------------------

class TestIsPrivateIp:
    @pytest.mark.parametrize("ip,expected", [
        ("10.0.0.5", True),
        ("172.16.0.1", True),
        ("172.32.0.1", False),
        ("192.168.1.1", True),
        ("8.8.8.8", False),
        ("127.0.0.1", True),
        ("not-an-ip", False),
    ])
    def test_is_private_ip(self, ip, expected):
        assert _is_private_ip(ip) == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
