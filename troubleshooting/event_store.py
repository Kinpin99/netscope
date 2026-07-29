import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from troubleshooting.syslog_parser import parse_syslog_line
from utils.config_loader import PROJECT_ROOT, load_config


class SyslogEventStore:
    def __init__(self, path: Optional[Path] = None):
        if path is None:
            cfg = load_config()
            raw = ((cfg.get("troubleshooting") or {}).get("syslog_events_file") or "data/syslogs/syslog_events.jsonl")
            path = Path(raw)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_raw(self, line: str, device_ip: Optional[str] = None) -> Dict[str, Any]:
        event = parse_syslog_line(line, default_device_ip=device_ip)
        return self.append_event(event)

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event.setdefault("received_at", time.time())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def list_events(self, since: Optional[float] = None, until: Optional[float] = None, limit: int = 500) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                ts = float(event.get("received_at") or event.get("timestamp") or 0)
                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
                rows.append(event)
        rows.sort(key=lambda e: e.get("received_at") or e.get("timestamp") or 0, reverse=True)
        return rows[:limit]
