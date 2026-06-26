
from typing import Optional


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
    """
    if score is None or score != score:  # NaN check without importing math/numpy
        return DEFAULT_SEVERITY

    for threshold, severity in SEVERITY_THRESHOLDS:
        if score >= threshold:
            return severity
    return DEFAULT_SEVERITY


def severity_rank(severity: str) -> int:
    """ranking for sorting and comparing severities (higher = more severe)."""
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0



ISSUE_TYPE_BANDWIDTH_CONGESTION = "network_congestion"
ISSUE_TYPE_PORTSCAN = "connectivity_security"
ISSUE_TYPE_DEVICE_BEHAVIOR = "device_environment"
ISSUE_TYPE_PROTOCOL = "network_performance"
ISSUE_TYPE_CAPACITY = "device_capacity"
ISSUE_TYPE_AUTH_FAILURE = "authentication_failure"  # syslog-derived, will add in future
ISSUE_TYPE_UNKNOWN = "unknown"

# Maps each of the four ML detectors to its primary issue_type category.
DETECTOR_ISSUE_TYPE = {
    "bandwidth": ISSUE_TYPE_BANDWIDTH_CONGESTION,
    "portscan": ISSUE_TYPE_PORTSCAN,
    "device_behavior": ISSUE_TYPE_DEVICE_BEHAVIOR,
    "protocol": ISSUE_TYPE_PROTOCOL,
}

CAPACITY_UTILIZATION_THRESHOLD = 0.85


def classify_issue_type(detector: str, feature_row: Optional[dict] = None) -> str:
    """
    Map a detector name (with optional feature values for that row) to one of
    the must_add_to_project.txt issue categories.
    """
    if detector == "bandwidth" and feature_row:
        util_in = feature_row.get("if_util_in", 0) or 0
        util_out = feature_row.get("if_util_out", 0) or 0
        if max(util_in, util_out) >= CAPACITY_UTILIZATION_THRESHOLD:
            return ISSUE_TYPE_CAPACITY
        return ISSUE_TYPE_BANDWIDTH_CONGESTION

    return DETECTOR_ISSUE_TYPE.get(detector, ISSUE_TYPE_UNKNOWN)


HEALTH_SCORE_WEIGHTS = {
    "bandwidth": 0.2,
    "portscan": 0.2,
    "device_behavior": 0.3,
    "protocol": 0.3,
}


def compute_health_score(detector_scores: dict) -> float:
    """
    Combine per-detector anomaly scores for one device into a single
    0-100 health score, where 100 is healthy and 0 is
    anomalous across all.
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


    if weighted_anomaly <= 0.5:
        return 100.0
    health = 100.0 * (1.0 - (weighted_anomaly - 0.5) / 0.5)
    return max(0.0, min(100.0, health))
