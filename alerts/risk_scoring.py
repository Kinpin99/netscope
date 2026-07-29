"""
risk_scoring.py
------------------
Pure functions for turning a raw anomaly_score (from ensemble_detector's
score_window, range [0,1] or NaN) into something a human can act on:

  - severity: info / low / medium / high / critical
  - issue_type: the categories from must_add_to_project.txt item 2
        ("connectivity, device environment, device capacity, network
        performance, and network status issues... including authentication
        failure, and network congestion")

No state, no I/O - alert_engine.py is the stateful layer that calls these
per-row and turns the results into Alert objects.
"""

from typing import Optional

# ---------------------------------------------------------------------------
# Severity thresholds
# ---------------------------------------------------------------------------
# anomaly_score is in [0,1] where ~0.5 is "typical" for an Isolation Forest
# on in-distribution data (see score_isolation_forest's clip(0.5 - raw, 0, 1)
# mapping - raw decision_function values cluster near 0 for normal points,
# giving scores near 0.5). Higher scores = more anomalous. These thresholds
# are deliberately conservative defaults; tune via config.yaml in a future
# pass once real alert volume is observed.
SEVERITY_THRESHOLDS = [
    (0.85, "critical"),
    (0.75, "high"),
    (0.65, "medium"),
    (0.55, "low"),
]
DEFAULT_SEVERITY = "info"

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def score_to_severity(score: Optional[float]) -> str:
    """
    Map an anomaly_score to a severity bucket.

    NaN/None (no model loaded yet, e.g. observation phase) maps to "info" -
    these rows should generally be filtered out of the alert feed entirely
    by alert_engine, but if they slip through they shouldn't look like a
    real alert.
    """
    if score is None or score != score:  # NaN check without importing math/numpy
        return DEFAULT_SEVERITY

    for threshold, severity in SEVERITY_THRESHOLDS:
        if score >= threshold:
            return severity
    return DEFAULT_SEVERITY


def severity_rank(severity: str) -> int:
    """Numeric rank for sorting/comparing severities (higher = more severe)."""
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Issue type classification (must_add_to_project.txt item 2)
#
# "Supports automatic identification of common network issues based on big
#  data and ML algorithms: connectivity, device environment, device
#  capacity, network performance, and network status issues. The issues
#  include authentication failure, and network congestion."
# ---------------------------------------------------------------------------

ISSUE_TYPE_BANDWIDTH_CONGESTION = "network_congestion"
ISSUE_TYPE_PORTSCAN = "connectivity_security"
ISSUE_TYPE_DEVICE_BEHAVIOR = "device_environment"
ISSUE_TYPE_PROTOCOL = "network_performance"
ISSUE_TYPE_CAPACITY = "device_capacity"
ISSUE_TYPE_AUTH_FAILURE = "authentication_failure"  # syslog-derived, future
ISSUE_TYPE_UNKNOWN = "unknown"

# Maps each of the four ML detectors to its primary issue_type category.
# This is the coarse classification; classify_issue_type further
# distinguishes "congestion" from "capacity" for the bandwidth detector
# using the underlying interface utilization feature, since both surface
# through the same model but mean different things to an admin.
DETECTOR_ISSUE_TYPE = {
    "bandwidth": ISSUE_TYPE_BANDWIDTH_CONGESTION,
    "portscan": ISSUE_TYPE_PORTSCAN,
    "device_behavior": ISSUE_TYPE_DEVICE_BEHAVIOR,
    "protocol": ISSUE_TYPE_PROTOCOL,
}

# If interface utilization (if_util_in/out, 0-1 fraction of link speed) is
# above this, classify a bandwidth anomaly as "device_capacity" (the link
# itself is saturated / near saturated) rather than "network_congestion"
# (a traffic spike that hasn't yet saturated the link, but deviates sharply
# from baseline).
CAPACITY_UTILIZATION_THRESHOLD = 0.85


def classify_issue_type(detector: str, feature_row: Optional[dict] = None) -> str:
    """
    Map a detector name (+ optional feature values for that row) to one of
    the must_add_to_project.txt issue categories.

    feature_row, if provided, should be the dict of feature column values
    for this row (e.g. from BandwidthFeatures.from_stream's output row) -
    used for the bandwidth congestion-vs-capacity refinement. If omitted,
    falls back to the coarse per-detector mapping.
    """
    if detector == "bandwidth" and feature_row:
        util_in = feature_row.get("if_util_in", 0) or 0
        util_out = feature_row.get("if_util_out", 0) or 0
        if max(util_in, util_out) >= CAPACITY_UTILIZATION_THRESHOLD:
            return ISSUE_TYPE_CAPACITY
        return ISSUE_TYPE_BANDWIDTH_CONGESTION

    return DETECTOR_ISSUE_TYPE.get(detector, ISSUE_TYPE_UNKNOWN)


# ---------------------------------------------------------------------------
# Network health score (must_add_to_project.txt item 3)
#
# "Intelligently analyses data sent from network devices and establishes a
#  network health evaluation system from multiple dimensions."
# ---------------------------------------------------------------------------

# Weight of each detector's contribution to a device's overall health score.
# Sums to 1.0. Device behavior and protocol anomalies are weighted slightly
# higher than bandwidth/portscan because they tend to indicate compromise or
# misconfiguration (more serious) vs. bandwidth/portscan which can be
# transient (a backup job, a vuln scanner running as part of normal ops).
HEALTH_SCORE_WEIGHTS = {
    "bandwidth": 0.2,
    "portscan": 0.2,
    "device_behavior": 0.3,
    "protocol": 0.3,
}


def compute_health_score(detector_scores: dict) -> float:
    """
    Combine per-detector anomaly scores for one device into a single
    0-100 health score, where 100 = perfectly healthy and 0 = maximally
    anomalous across all dimensions.

    detector_scores: {"bandwidth": 0.5, "portscan": 0.6, ...} - anomaly
    scores in [0,1] (NaN entries are ignored and that detector's weight is
    redistributed proportionally across the remaining detectors, so a
    device isn't penalized just because a model hasn't been trained yet).

    Returns a value in [0, 100].
    """
    valid = {k: v for k, v in detector_scores.items() if v is not None and v == v}
    if not valid:
        return 100.0  # no signal at all -> assume healthy, not unhealthy

    total_weight = sum(HEALTH_SCORE_WEIGHTS.get(k, 0) for k in valid)
    if total_weight == 0:
        # detectors present but none have configured weights - equal split
        weighted_anomaly = sum(valid.values()) / len(valid)
    else:
        weighted_anomaly = sum(
            v * HEALTH_SCORE_WEIGHTS.get(k, 0) for k, v in valid.items()
        ) / total_weight

    # anomaly_score ~0.5 is "typical/normal" (see SEVERITY_THRESHOLDS comment).
    # Map [0.5, 1.0] -> [100, 0] linearly; anything below 0.5 (less anomalous
    # than typical) clamps to 100 (perfectly healthy).
    if weighted_anomaly <= 0.5:
        return 100.0
    health = 100.0 * (1.0 - (weighted_anomaly - 0.5) / 0.5)
    return max(0.0, min(100.0, health))
