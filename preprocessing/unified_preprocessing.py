import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import entropy as scipy_entropy

warnings.filterwarnings("ignore", category=RuntimeWarning)


# Shared constants
TIME_WINDOW_SEC = 60          # primary aggregation window

PROTOCOL_TCP  = 6
PROTOCOL_UDP  = 17
PROTOCOL_ICMP = 1

TCP_FLAG_SYN = 0x02
TCP_FLAG_ACK = 0x10
TCP_FLAG_FIN = 0x01
TCP_FLAG_RST = 0x04

WELL_KNOWN_PORT_MAX = 1024



# Shared helpers
def _load_netflow(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("netflow_raw_*.csv"))
        if not files:
            return pd.DataFrame(columns=[
                "timestamp", "src_ip", "dst_ip", "src_port", "dst_port",
                "protocol", "tcp_flags", "packets", "bytes", "duration_sec",
            ])
        df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    else:
        df = pd.read_csv(path)

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    return df


def _load_snmp(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("prtg_raw_*.csv")) or sorted(p.glob("snmp_raw_*.csv"))
        if not files:
            return pd.DataFrame(columns=[
                "timestamp", "device_ip", "if_in_octets", "if_out_octets",
                "if_speed", "if_in_errors", "cpu_load_pct", "mem_used_pct",
            ])
        df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    else:
        if not p.exists():
            return pd.DataFrame(columns=[
                "timestamp", "device_ip", "if_in_octets", "if_out_octets",
                "if_speed", "if_in_errors", "cpu_load_pct", "mem_used_pct",
            ])
        df = pd.read_csv(path)

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    return df


def _assign_window(df: pd.DataFrame, window_sec: int = TIME_WINDOW_SEC) -> pd.DataFrame:
    """Add a 'window' column (Unix epoch of the window start)."""
    df = df.copy()
    df["window"] = (df["timestamp"] // window_sec).astype(int) * window_sec
    return df


def _assign_device_ip(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    is_inbound = df["dst_ip"].apply(_is_private_ip).astype(bool)
    df["device_ip"] = np.where(is_inbound, df["dst_ip"], df["src_ip"])
    return df


def _safe_entropy(series: pd.Series) -> float:
    counts = series.value_counts(normalize=True)
    return float(scipy_entropy(counts))


def _zscore_col(series: pd.Series) -> pd.Series:
    mu, sigma = series.mean(), series.std()
    if sigma == 0:
        return pd.Series(0.0, index=series.index)
    return (series - mu) / sigma


def _rolling_zscore(
    df: pd.DataFrame, group_col: str, value_col: str,
    window: int = 1440,                    # minutes of history
) -> pd.Series:

    result = pd.Series(np.nan, index=df.index)
    for grp, gdf in df.groupby(group_col):
        vals = gdf[value_col]
        roll_mean = vals.shift(1).rolling(window, min_periods=5).mean()
        roll_std  = vals.shift(1).rolling(window, min_periods=5).std()
        z = (vals - roll_mean) / roll_std.replace(0, np.nan)
        result.loc[gdf.index] = z
    return result


def _zscore_from_stats(value: float, mean: float, std: float) -> float:
    if std is None or std == 0 or pd.isna(std) or pd.isna(mean):
        return 0.0
    return (value - mean) / std


def _apply_stats_zscore(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    stats: Dict[str, Dict[str, float]],
    out_col: Optional[str] = None,
) -> pd.Series:
    out_col = out_col or f"{value_col}_zscore"
    means = df[group_col].map(lambda g: stats.get(g, {}).get(f"{value_col}_mean"))
    stds  = df[group_col].map(lambda g: stats.get(g, {}).get(f"{value_col}_std"))
    return pd.Series(
        [
            _zscore_from_stats(v, m, s)
            for v, m, s in zip(df[value_col], means, stds)
        ],
        index=df.index,
        name=out_col,
    )


def compute_normalization_stats(
    feat_df: pd.DataFrame,
    group_col: str,
    value_cols: List[str],
) -> Dict[str, Dict[str, float]]:
    stats: Dict[str, Dict[str, float]] = {}
    for grp, gdf in feat_df.groupby(group_col):
        entry = {}
        for col in value_cols:
            if col in gdf.columns:
                entry[f"{col}_mean"] = float(gdf[col].mean())
                entry[f"{col}_std"] = float(gdf[col].std())
        stats[str(grp)] = entry
    return stats



# 1. BandwidthFeatures
class BandwidthFeatures:
    WINDOW_SEC = TIME_WINDOW_SEC

    # For z-score rolling history: number of windows to look back - training only
    ROLLING_WINDOW = 1440  # 1 day if windows are 1 min each

    ZSCORE_VALUE_COLS = ["bw_in_bytes", "bw_out_bytes"]

    @classmethod
    def from_csv(cls, netflow_csv: str, snmp_csv: str) -> pd.DataFrame:
        """Load raw CSVs and compute features, including rolling z-scores. For training."""
        nf = _load_netflow(netflow_csv)
        snmp = _load_snmp(snmp_csv)
        feat = cls._compute(nf, snmp)
        feat = feat.sort_values(["device_ip", "window"]).reset_index(drop=True)
        feat["bw_in_zscore"]  = _rolling_zscore(feat, "device_ip", "bw_in_bytes",  cls.ROLLING_WINDOW)
        feat["bw_out_zscore"] = _rolling_zscore(feat, "device_ip", "bw_out_bytes", cls.ROLLING_WINDOW)
        feat[["bw_in_zscore", "bw_out_zscore"]] = feat[["bw_in_zscore", "bw_out_zscore"]].fillna(0)
        return feat[cls._output_columns()]

    @classmethod
    def from_stream(
        cls,
        netflow_df: pd.DataFrame,
        snmp_df: pd.DataFrame,
        normalization_stats: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> pd.DataFrame:
        """
        Compute features from already-loaded DataFrames. For live inference.
        """
        feat = cls._compute(netflow_df.copy(), snmp_df.copy())
        stats = (normalization_stats or {}).get("bandwidth", {})
        feat["bw_in_zscore"]  = _apply_stats_zscore(feat, "device_ip", "bw_in_bytes",  stats, "bw_in_zscore")
        feat["bw_out_zscore"] = _apply_stats_zscore(feat, "device_ip", "bw_out_bytes", stats, "bw_out_zscore")
        return feat[cls._output_columns()]

    @classmethod
    def _output_columns(cls) -> List[str]:
        return [
            "device_ip", "window",
            "bw_in_bytes", "bw_out_bytes",
            "bw_in_pkts",  "bw_out_pkts",
            "bw_in_rate_bps", "bw_out_rate_bps",
            "bw_in_zscore",   "bw_out_zscore",
            "if_util_in",     "if_util_out",
            "if_errors_delta",
            "cpu_load_pct",   "mem_used_pct",
        ]

    @classmethod
    def _compute(cls, nf: pd.DataFrame, snmp: pd.DataFrame) -> pd.DataFrame:
        nf = _assign_window(nf, cls.WINDOW_SEC)

        # Determine direction relative to the monitored network, and which
        # IP the flow's traffic should be attributed to
        nf["is_inbound"] = nf["dst_ip"].apply(_is_private_ip).astype(bool)
        nf = _assign_device_ip(nf)

        in_flows  = nf[nf["is_inbound"]]
        out_flows = nf[~nf["is_inbound"]]

        def agg_dir(flows):
            return flows.groupby(["device_ip", "window"]).agg(
                bytes_sum=("bytes", "sum"),
                pkts_sum=("packets", "sum"),
            )

        in_agg  = agg_dir(in_flows).rename(columns={"bytes_sum": "bw_in_bytes", "pkts_sum": "bw_in_pkts"})
        out_agg = agg_dir(out_flows).rename(columns={"bytes_sum": "bw_out_bytes", "pkts_sum": "bw_out_pkts"})

        feat = in_agg.join(out_agg, how="outer").fillna(0).reset_index()
        feat["bw_in_rate_bps"]  = feat["bw_in_bytes"]  * 8 / cls.WINDOW_SEC
        feat["bw_out_rate_bps"] = feat["bw_out_bytes"] * 8 / cls.WINDOW_SEC

        # --- SNMP/PRTG join ---
        if not snmp.empty and "device_ip" in snmp.columns:
            snmp = _assign_window(snmp, cls.WINDOW_SEC)
            snmp_grp = snmp.groupby(["device_ip", "window"]).agg(
                if_in_octets=("if_in_octets",  "sum"),
                if_out_octets=("if_out_octets", "sum"),
                if_speed=("if_speed", "max"),
                if_errors_delta=(
                    "if_in_errors",
                    lambda x: x.max() - x.min()
                ),
                cpu_load_pct=("cpu_load_pct", "mean"),
                mem_used_pct=("mem_used_pct",  "mean"),
            ).reset_index()

            snmp_grp["if_util_in"] = np.where(
                snmp_grp["if_speed"] > 0,
                (snmp_grp["if_in_octets"] * 8) / (cls.WINDOW_SEC * snmp_grp["if_speed"]),
                np.nan
            )
            snmp_grp["if_util_out"] = np.where(
                snmp_grp["if_speed"] > 0,
                (snmp_grp["if_out_octets"] * 8) / (cls.WINDOW_SEC * snmp_grp["if_speed"]),
                np.nan
            )

            feat = feat.merge(
                snmp_grp[["device_ip", "window", "if_util_in", "if_util_out",
                           "if_errors_delta", "cpu_load_pct", "mem_used_pct"]],
                on=["device_ip", "window"], how="left"
            )
        else:
            feat["if_util_in"] = np.nan
            feat["if_util_out"] = np.nan
            feat["if_errors_delta"] = np.nan
            feat["cpu_load_pct"] = np.nan
            feat["mem_used_pct"] = np.nan

        feat = feat.fillna(0)
        return feat



# 2. PortScanFeatures
class PortScanFeatures:
    WINDOW_SEC = TIME_WINDOW_SEC
    SMALL_FLOW_THRESHOLD_SEC = 3.0

    @classmethod
    def from_csv(cls, netflow_csv: str) -> pd.DataFrame:
        """PortScan detection is NetFlow-only so no SNMP/PRTG input needed."""
        nf = _load_netflow(netflow_csv)
        return cls._compute(nf)

    @classmethod
    def from_stream(cls, netflow_df: pd.DataFrame) -> pd.DataFrame:
        return cls._compute(netflow_df.copy())

    @classmethod
    def _compute(cls, nf: pd.DataFrame) -> pd.DataFrame:
        nf = _assign_window(nf, cls.WINDOW_SEC)
        nf["is_syn_only"] = (
            (nf["protocol"] == PROTOCOL_TCP) &
            ((nf["tcp_flags"] & TCP_FLAG_SYN) > 0) &
            ((nf["tcp_flags"] & TCP_FLAG_ACK) == 0)
        )
        nf["is_established"] = (
            (nf["protocol"] == PROTOCOL_TCP) &
            (((nf["tcp_flags"] & TCP_FLAG_FIN) > 0) |
             ((nf["tcp_flags"] & TCP_FLAG_RST) > 0))
        )
        nf["is_udp"]         = (nf["protocol"] == PROTOCOL_UDP)
        nf["is_small_flow"]  = (nf["duration_sec"] < cls.SMALL_FLOW_THRESHOLD_SEC)
        nf["is_well_known"]  = (nf["dst_port"] < WELL_KNOWN_PORT_MAX)

        rows = []
        for (src_ip, window), grp in nf.groupby(["src_ip", "window"]):
            flows_total = len(grp)
            rows.append({
                "src_ip":              src_ip,
                "window":              window,
                "flows_total":         flows_total,
                "flows_per_sec":       round(flows_total / cls.WINDOW_SEC, 4),
                "distinct_dst_ports":  grp["dst_port"].nunique(),
                "distinct_dst_ips":    grp["dst_ip"].nunique(),
                "port_entropy":        round(_safe_entropy(grp["dst_port"]), 4),
                "tcp_syn_ratio":       round(grp["is_syn_only"].sum() / max(flows_total, 1), 4),
                "udp_ratio":           round(grp["is_udp"].sum() / max(flows_total, 1), 4),
                "success_rate":        round(grp["is_established"].sum() / max(flows_total, 1), 4),
                "small_flow_ratio":    round(grp["is_small_flow"].sum() / max(flows_total, 1), 4),
                "well_known_port_ratio": round(grp["is_well_known"].sum() / max(flows_total, 1), 4),
            })

        if not rows:
            return pd.DataFrame(columns=[
                "src_ip", "window", "flows_total", "flows_per_sec",
                "distinct_dst_ports", "distinct_dst_ips", "port_entropy",
                "tcp_syn_ratio", "udp_ratio", "success_rate",
                "small_flow_ratio", "well_known_port_ratio",
            ])

        return pd.DataFrame(rows)



# 3. DeviceBehaviorFeatures
class DeviceBehaviorFeatures:
    WINDOW_SEC    = TIME_WINDOW_SEC
    ROLLING_WINDOW = 1440

    ZSCORE_VALUE_COLS = ["bytes_in", "bytes_out", "distinct_dst_ips", "cpu_load_pct", "mem_used_pct"]

    @classmethod
    def from_csv(cls, netflow_csv: str, snmp_csv: str) -> pd.DataFrame:
        nf   = _load_netflow(netflow_csv)
        snmp = _load_snmp(snmp_csv)
        feat = cls._compute(nf, snmp)

        feat = feat.sort_values(["device_ip", "window"]).reset_index(drop=True)
        feat["bytes_in_zscore"]         = _rolling_zscore(feat, "device_ip", "bytes_in",         cls.ROLLING_WINDOW)
        feat["bytes_out_zscore"]        = _rolling_zscore(feat, "device_ip", "bytes_out",        cls.ROLLING_WINDOW)
        feat["distinct_dst_ips_zscore"] = _rolling_zscore(feat, "device_ip", "distinct_dst_ips", cls.ROLLING_WINDOW)
        feat["cpu_util_zscore"]         = _rolling_zscore(feat, "device_ip", "cpu_load_pct",     cls.ROLLING_WINDOW)
        feat["mem_util_zscore"]         = _rolling_zscore(feat, "device_ip", "mem_used_pct",     cls.ROLLING_WINDOW)

        zcols = ["bytes_in_zscore", "bytes_out_zscore", "distinct_dst_ips_zscore",
                 "cpu_util_zscore", "mem_util_zscore"]
        feat[zcols] = feat[zcols].fillna(0)
        return feat[cls._output_columns()]

    @classmethod
    def from_stream(
        cls,
        netflow_df: pd.DataFrame,
        snmp_df: pd.DataFrame,
        normalization_stats: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> pd.DataFrame:
        feat = cls._compute(netflow_df.copy(), snmp_df.copy())
        stats = (normalization_stats or {}).get("device_behavior", {})

        feat["bytes_in_zscore"]         = _apply_stats_zscore(feat, "device_ip", "bytes_in",         stats, "bytes_in_zscore")
        feat["bytes_out_zscore"]        = _apply_stats_zscore(feat, "device_ip", "bytes_out",        stats, "bytes_out_zscore")
        feat["distinct_dst_ips_zscore"] = _apply_stats_zscore(feat, "device_ip", "distinct_dst_ips", stats, "distinct_dst_ips_zscore")
        feat["cpu_util_zscore"]         = _apply_stats_zscore(feat, "device_ip", "cpu_load_pct",     stats, "cpu_util_zscore")
        feat["mem_util_zscore"]         = _apply_stats_zscore(feat, "device_ip", "mem_used_pct",     stats, "mem_util_zscore")
        return feat[cls._output_columns()]

    @classmethod
    def _output_columns(cls) -> List[str]:
        return [
            "device_ip", "window",
            "bytes_in", "bytes_out",
            "bytes_in_zscore", "bytes_out_zscore",
            "tcp_ratio", "udp_ratio", "icmp_ratio",
            "distinct_dst_ips", "distinct_dst_ips_zscore",
            "hour_sin", "hour_cos",
            "cpu_util_zscore", "mem_util_zscore",
        ]

    @classmethod
    def _compute(cls, nf: pd.DataFrame, snmp: pd.DataFrame) -> pd.DataFrame:
        nf = _assign_window(nf, cls.WINDOW_SEC)
        nf = _assign_device_ip(nf)   # shared helper - handles inbound-from-external correctly

        nf["is_tcp"]  = (nf["protocol"] == PROTOCOL_TCP)
        nf["is_udp"]  = (nf["protocol"] == PROTOCOL_UDP)
        nf["is_icmp"] = (nf["protocol"] == PROTOCOL_ICMP)

        is_inbound = nf["dst_ip"].apply(_is_private_ip).astype(bool)
        in_flows  = nf[is_inbound]
        out_flows = nf[~is_inbound]

        in_bytes  = in_flows.groupby(["device_ip", "window"])["bytes"].sum().rename("bytes_in")
        out_bytes = out_flows.groupby(["device_ip", "window"])["bytes"].sum().rename("bytes_out")

        grp = nf.groupby(["device_ip", "window"])
        total_flows = grp.size().rename("total_flows")
        tcp_cnt     = nf[nf["is_tcp"]].groupby(["device_ip",  "window"]).size().rename("tcp_cnt")
        udp_cnt     = nf[nf["is_udp"]].groupby(["device_ip",  "window"]).size().rename("udp_cnt")
        icmp_cnt    = nf[nf["is_icmp"]].groupby(["device_ip", "window"]).size().rename("icmp_cnt")
        dst_ips     = grp["dst_ip"].nunique().rename("distinct_dst_ips")

        feat = pd.concat([in_bytes, out_bytes, total_flows,
                          tcp_cnt, udp_cnt, icmp_cnt, dst_ips], axis=1).fillna(0).reset_index()

        feat["tcp_ratio"]  = feat["tcp_cnt"]  / feat["total_flows"].replace(0, 1)
        feat["udp_ratio"]  = feat["udp_cnt"]  / feat["total_flows"].replace(0, 1)
        feat["icmp_ratio"] = feat["icmp_cnt"] / feat["total_flows"].replace(0, 1)

        # Time cyclical encoding
        feat["hour"] = feat["window"].apply(lambda t: (t % 86400) / 3600)
        feat["hour_sin"] = np.sin(2 * np.pi * feat["hour"] / 24)
        feat["hour_cos"] = np.cos(2 * np.pi * feat["hour"] / 24)

        # SNMP/PRTG join
        if not snmp.empty and "device_ip" in snmp.columns:
            snmp = _assign_window(snmp, cls.WINDOW_SEC)
            snmp_grp = snmp.groupby(["device_ip", "window"]).agg(
                cpu_load_pct=("cpu_load_pct", "mean"),
                mem_used_pct=("mem_used_pct", "mean"),
            ).reset_index()

            feat = feat.merge(
                snmp_grp[["device_ip", "window", "cpu_load_pct", "mem_used_pct"]],
                on=["device_ip", "window"], how="left"
            )
        else:
            feat["cpu_load_pct"] = np.nan
            feat["mem_used_pct"] = np.nan

        feat = feat.fillna(0)
        return feat



# 4. ProtocolFeatures
class ProtocolFeatures:
    WINDOW_SEC = TIME_WINDOW_SEC

    # Well-known port: expected protocol mapping for mismatch detection
    PORT_PROTOCOL_EXPECTED: Dict[int, int] = {
        53:  PROTOCOL_UDP,   # DNS (TCP/53 tunneling is suspicious)
        80:  PROTOCOL_TCP,
        443: PROTOCOL_TCP,
        25:  PROTOCOL_TCP,   # SMTP
        22:  PROTOCOL_TCP,   # SSH
        23:  PROTOCOL_TCP,   # Telnet
        67:  PROTOCOL_UDP,   # DHCP
        68:  PROTOCOL_UDP,
        123: PROTOCOL_UDP,   # NTP
    }

    @classmethod
    def from_csv(cls, netflow_csv: str, baseline_csv: Optional[str] = None) -> pd.DataFrame:
        nf = _load_netflow(netflow_csv)
        baseline = _load_baseline(baseline_csv) if baseline_csv else {}
        return cls._compute(nf, baseline)

    @classmethod
    def from_stream(cls, netflow_df: pd.DataFrame,
                    baseline: Optional[Dict] = None) -> pd.DataFrame:
        return cls._compute(netflow_df.copy(), baseline or {})

    @classmethod
    def _compute(cls, nf: pd.DataFrame, baseline: dict) -> pd.DataFrame:
        nf = _assign_window(nf, cls.WINDOW_SEC)
        nf = _assign_device_ip(nf)   # shared helper

        nf["is_tcp"]   = (nf["protocol"] == PROTOCOL_TCP)
        nf["is_udp"]   = (nf["protocol"] == PROTOCOL_UDP)
        nf["is_icmp"]  = (nf["protocol"] == PROTOCOL_ICMP)
        nf["is_other"] = ~(nf["is_tcp"] | nf["is_udp"] | nf["is_icmp"])

        # Port-protocol mismatch flag
        nf["port_mismatch"] = nf.apply(
            lambda r: int(
                r["dst_port"] in cls.PORT_PROTOCOL_EXPECTED and
                cls.PORT_PROTOCOL_EXPECTED[r["dst_port"]] != r["protocol"]
            ),
            axis=1
        )

        # Per-packet size proxies
        nf["pkt_size"] = np.where(
            nf["packets"] > 0,
            nf["bytes"] / nf["packets"],
            0
        )

        rows = []
        for (device_ip, window), grp in nf.groupby(["device_ip", "window"]):
            n = len(grp)
            tcp_r  = grp["is_tcp"].sum()   / n
            udp_r  = grp["is_udp"].sum()   / n
            icmp_r = grp["is_icmp"].sum()  / n
            other_r= grp["is_other"].sum() / n

            current_dist = {
                PROTOCOL_TCP:  tcp_r,
                PROTOCOL_UDP:  udp_r,
                PROTOCOL_ICMP: icmp_r,
            }

            # KL divergence vs baseline
            kl = 0.0
            if device_ip in baseline:
                kl = _kl_divergence(current_dist, baseline[device_ip])

            # Protocols present in this window but not in baseline
            if device_ip in baseline:
                baseline_protos = set(
                    p for p, r in baseline[device_ip].items() if r > 0
                )
                current_protos = set(grp["protocol"].unique())
                num_new = len(current_protos - baseline_protos)
            else:
                num_new = 0

            tcp_grp = grp[grp["is_tcp"]]
            udp_grp = grp[grp["is_udp"]]

            rows.append({
                "device_ip":                  device_ip,
                "window":                     window,
                "protocol_entropy":           round(_safe_entropy(grp["protocol"]), 4),
                "tcp_ratio":                  round(tcp_r, 4),
                "udp_ratio":                  round(udp_r, 4),
                "icmp_ratio":                 round(icmp_r, 4),
                "other_ratio":                round(other_r, 4),
                "num_new_protocols":          num_new,
                "port_protocol_mismatch_count": int(grp["port_mismatch"].sum()),
                "avg_pkt_size_tcp":           round(tcp_grp["pkt_size"].mean(), 2) if len(tcp_grp) else 0.0,
                "avg_pkt_size_udp":           round(udp_grp["pkt_size"].mean(), 2) if len(udp_grp) else 0.0,
                "kl_div_from_baseline":       round(kl, 6),
            })

        if not rows:
            return pd.DataFrame(columns=[
                "device_ip", "window", "protocol_entropy",
                "tcp_ratio", "udp_ratio", "icmp_ratio", "other_ratio",
                "num_new_protocols", "port_protocol_mismatch_count",
                "avg_pkt_size_tcp", "avg_pkt_size_udp", "kl_div_from_baseline",
            ])

        return pd.DataFrame(rows)



# Helpers
def _is_private_ip(ip: str) -> bool:
    """Return True if IP is RFC-1918 private or loopback."""
    try:
        parts = list(map(int, ip.split(".")))
        if parts[0] == 10:
            return True
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return True
        if parts[0] == 192 and parts[1] == 168:
            return True
        if parts[0] == 127:
            return True
        return False
    except Exception:
        return False


def _kl_divergence(p: dict, q: dict) -> float:
    """KL(P || Q) for protocol distributions. Handles zero entries with smoothing."""
    all_keys = set(p) | set(q)
    eps = 1e-9
    kl = 0.0
    for k in all_keys:
        pi = p.get(k, 0) + eps
        qi = q.get(k, 0) + eps
        kl += pi * math.log(pi / qi)
    return kl


def _load_baseline(path: str) -> dict:

    if not Path(path).exists():
        return {}

    df = pd.read_csv(path)
    baseline = {}
    for _, row in df.iterrows():
        dev = row["device_ip"]
        proto = int(row["protocol"])
        ratio = float(row["ratio"])
        if dev not in baseline:
            baseline[dev] = {}
        baseline[dev][proto] = ratio
    return baseline



# Convenience: build all four feature sets in one call
def build_all_normalization_stats(features: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Dict[str, float]]]:
    stats: Dict[str, Dict[str, Dict[str, float]]] = {}

    if "bandwidth" in features and not features["bandwidth"].empty:
        stats["bandwidth"] = compute_normalization_stats(
            features["bandwidth"], "device_ip", BandwidthFeatures.ZSCORE_VALUE_COLS
        )

    if "device_behavior" in features and not features["device_behavior"].empty:
        stats["device_behavior"] = compute_normalization_stats(
            features["device_behavior"], "device_ip", DeviceBehaviorFeatures.ZSCORE_VALUE_COLS
        )

    return stats


def build_all_features(
    netflow_csv: str,
    snmp_csv: str,
    protocol_baseline_csv: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Compute all four feature DataFrames from raw CSVs. For training.
    Returns a dict keyed by detector name.
    """
    return {
        "bandwidth":       BandwidthFeatures.from_csv(netflow_csv, snmp_csv),
        "portscan":        PortScanFeatures.from_csv(netflow_csv),
        "device_behavior": DeviceBehaviorFeatures.from_csv(netflow_csv, snmp_csv),
        "protocol":        ProtocolFeatures.from_csv(netflow_csv, protocol_baseline_csv),
    }
