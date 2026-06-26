import argparse
import csv
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from project root or collectors/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collectors.packet_utils import (
    parse_netflow_v5,
    parse_netflow_v9,
    parse_pcap_file,
)


# Config
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 2055
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_FILE_PREFIX = "netflow_raw"
BUFFER_SIZE = 65535

CSV_FIELDS = [
    "timestamp", "src_ip", "dst_ip",
    "src_port", "dst_port", "protocol",
    "tcp_flags", "packets", "bytes", "duration_sec",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [netflow] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)



# Writer - daily-rotated, append-safe CSV
class RotatingCsvWriter:


    def __init__(self, output_dir: Path, prefix: str = DEFAULT_FILE_PREFIX):
        self.output_dir = output_dir
        self.prefix = prefix
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = None
        self._current_path = None
        self._write_header = False

    def _path_for(self, dt: datetime) -> Path:
        return self.output_dir / f"{self.prefix}_{dt.strftime('%Y-%m-%d')}.csv"

    def _ensure_current_file(self, ts: float) -> Path:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_key = dt.date()
        if date_key != self._current_date:
            self._current_date = date_key
            self._current_path = self._path_for(dt)
            self._write_header = (
                not self._current_path.exists()
                or self._current_path.stat().st_size == 0
            )
            log.info("Rotating output file -> %s", self._current_path)
        return self._current_path

    def write_records(self, records: list) -> None:
        if not records:
            return
        # Use the timestamp of the first record to decide the file for this batch
        path = self._ensure_current_file(records[0].timestamp)
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if self._write_header:
                writer.writeheader()
                self._write_header = False
            for rec in records:
                if rec is not None:
                    writer.writerow(rec.to_csv_row())



# Optional Kafka publisher (Phase 3 - live inference)
class KafkaFlowPublisher:
    def __init__(self, bootstrap_servers: str, topic: str):
        from kafka import KafkaProducer  # local import: optional dependency

        self.topic = topic
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            linger_ms=50,
        )
        log.info("Kafka publisher ready -> topic=%s bootstrap=%s", topic, bootstrap_servers)

    def publish(self, records: list) -> None:
        for rec in records:
            if rec is not None:
                self.producer.send(self.topic, value=rec.to_csv_row())

    def flush(self) -> None:
        self.producer.flush()

    def close(self) -> None:
        self.producer.flush()
        self.producer.close()


# UDP socket mode
def run_udp(
    host: str,
    port: int,
    output_dir: Path,
    kafka_publisher: "KafkaFlowPublisher | None" = None,
    write_csv: bool = True,
) -> None:
    writer = RotatingCsvWriter(output_dir) if write_csv else None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))

    mode_desc = []
    if write_csv:
        mode_desc.append(f"CSV -> {output_dir}")
    if kafka_publisher:
        mode_desc.append(f"Kafka -> {kafka_publisher.topic}")
    log.info("Listening for NetFlow exports on %s:%d [%s]", host, port, ", ".join(mode_desc))

    total = 0
    try:
        while True:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            recv_time = time.time()
            exporter_ip = addr[0]

            if len(data) < 2:
                continue

            version = int.from_bytes(data[:2], "big")
            if version == 5:
                records = parse_netflow_v5(data, recv_time)
            elif version == 9:
                # source_addr keys the per-exporter template cache, so two
                # routers reusing template_id=256 don't clobber each other.
                records = parse_netflow_v9(data, recv_time, source_addr=exporter_ip)
            else:
                log.debug("Unsupported NetFlow version %d from %s", version, addr)
                continue

            if records:
                if writer:
                    writer.write_records(records)
                if kafka_publisher:
                    kafka_publisher.publish(records)
                total += len(records)
                log.info("Received %d flows from %s (total=%d)", len(records), exporter_ip, total)

    except KeyboardInterrupt:
        log.info("Shutting down. Total flows collected: %d", total)
    finally:
        sock.close()
        if kafka_publisher:
            kafka_publisher.close()



# pcap mode
def run_pcap(pcap_file: str, output_dir: Path) -> None:
    if not os.path.exists(pcap_file):
        log.error("pcap file not found: %s", pcap_file)
        sys.exit(1)

    writer = RotatingCsvWriter(output_dir)
    log.info("Parsing pcap file: %s -> %s", pcap_file, output_dir)

    total = 0
    batch = []
    BATCH_SIZE = 500

    for rec in parse_pcap_file(pcap_file):
        if rec is not None:
            batch.append(rec)
            if len(batch) >= BATCH_SIZE:
                writer.write_records(batch)
                total += len(batch)
                batch = []

    if batch:
        writer.write_records(batch)
        total += len(batch)

    log.info("Done. Total flows written: %d", total)



# CLI
def main():
    parser = argparse.ArgumentParser(description="NetFlow collector (UDP or pcap)")
    parser.add_argument(
        "--mode", choices=["udp", "pcap"], required=True,
        help="Collection mode: 'udp' for live, 'pcap' for offline file"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="UDP bind address (udp mode)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port (udp mode)")
    parser.add_argument("--file", default=None, help="Path to .pcap file (pcap mode)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help="Output directory for rotated daily CSVs")
    parser.add_argument("--no-csv", action="store_true",
                        help="Disable CSV output (Kafka only, live inference)")
    parser.add_argument("--publish-kafka", action="store_true",
                        help="Also publish each flow record to Kafka (Phase 3)")
    parser.add_argument("--kafka-bootstrap", default="localhost:9092",
                        help="Kafka bootstrap servers")
    parser.add_argument("--kafka-topic", default="netflow-raw",
                        help="Kafka topic to publish flow records to")

    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    kafka_publisher = None
    if args.publish_kafka:
        kafka_publisher = KafkaFlowPublisher(args.kafka_bootstrap, args.kafka_topic)

    if args.mode == "udp":
        run_udp(
            args.host, args.port, output_dir,
            kafka_publisher=kafka_publisher,
            write_csv=not args.no_csv,
        )
    else:
        if not args.file:
            parser.error("--file is required for pcap mode")
        run_pcap(args.file, output_dir)


if __name__ == "__main__":
    main()
