"""
config_loader.py
-----------------
Shared config loading for the whole project. Reads config.yaml from the
project root and resolves a couple of conveniences:

  - PRTG API token: env var PRTG_API_TOKEN takes precedence over
    config.yaml's prtg.api_token, so the token never has to be committed.
  - Relative paths under `paths:` are resolved to absolute paths anchored
    at the project root, so collectors/training scripts work regardless
    of the working directory they're launched from.

Usage:
    from utils.config_loader import load_config
    cfg = load_config()
    cfg["prtg"]["base_url"]
    cfg["paths"]["netflow_raw_dir"]   # -> absolute Path
    cfg["devices"]                    # -> list of device dicts
"""

import os
from pathlib import Path
from typing import Any, Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config(path: "str | Path | None" = None) -> Dict[str, Any]:
    """
    Load and lightly post-process config.yaml.

    Returns a dict with the same top-level keys as config.yaml
    (system, prtg, devices, bootstrap, paths), plus:
      - cfg["prtg"]["api_token"] overridden by PRTG_API_TOKEN env var if set
      - cfg["paths"][...] converted from relative strings to absolute Path
        objects, anchored at the project root
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    # Record the resolved path so callers that spawn subprocesses
    # (orchestrator.SystemOrchestrator) can pass --config explicitly,
    # ensuring the subprocess sees the same config even if
    # DEFAULT_CONFIG_PATH was monkeypatched (e.g. in tests) or a relative
    # path was given.
    cfg["_config_path"] = str(cfg_path.resolve())

    # Resolve PRTG token from env, falling back to whatever is in the file
    env_token = os.environ.get("PRTG_API_TOKEN")
    if env_token:
        cfg.setdefault("prtg", {})["api_token"] = env_token

    # Resolve data paths to absolute Path objects
    paths = cfg.get("paths", {})
    for key, val in paths.items():
        p = Path(val)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        paths[key] = p
    cfg["paths"] = paths

    return cfg


def get_device_by_ip(cfg: Dict[str, Any], ip: str) -> "Dict[str, Any] | None":
    """Convenience lookup: find a device's config entry by its IP."""
    for dev in cfg.get("devices", []):
        if dev.get("ip") == ip:
            return dev
    return None


def all_device_ips(cfg: Dict[str, Any]) -> list:
    return [dev["ip"] for dev in cfg.get("devices", []) if "ip" in dev]
