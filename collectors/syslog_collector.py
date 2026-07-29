"""
collectors/syslog_collector.py
------------------------------
Simple UDP syslog receiver for the automated troubleshooting module.

Production devices would normally forward syslogs to a SIEM or syslog server.
For this project, this collector listens on a UDP port, normalises received
messages, and appends them to data/syslogs/syslog_events.jsonl so the
NetworkX-based troubleshooting engine can correlate them with alerts and
telemetry.

Example:
    python collectors/syslog_collector.py --host 0.0.0.0 --port 5514
"""

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from troubleshooting.event_store import SyslogEventStore


def run(host: str, port: int) -> None:
    store = SyslogEventStore()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print("Syslog collector listening on {}:{}".format(host, port))
    print("Writing events to {}".format(store.path))
    while True:
        data, addr = sock.recvfrom(65535)
        try:
            line = data.decode("utf-8", errors="replace").strip()
        except TypeError:  # Python 3.5 compatibility edge case
            line = str(data)
        if not line:
            continue
        event = store.append_raw(line, device_ip=addr[0])
        print("{} {} {}".format(event.get("severity"), event.get("event_type"), event.get("device_ip")))


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP syslog collector for troubleshooting correlation")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5514, help="Use 5514 for non-root lab runs; 514 usually requires admin/root.")
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
