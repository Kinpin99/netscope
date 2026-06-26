import struct
import socket
from dataclasses import dataclass
from typing import List, Optional



# NetFlow v5 constants
NF5_HEADER_SIZE = 24   # bytes
NF5_RECORD_SIZE = 48   # bytes

# NetFlow v9 field type IDs (partial, most common)
NF9_FIELD_TYPES = {
    1:  "IN_BYTES",
    2:  "IN_PKTS",
    4:  "PROTOCOL",
    5:  "TOS",
    6:  "TCP_FLAGS",
    7:  "L4_SRC_PORT",
    8:  "IPV4_SRC_ADDR",
    11: "L4_DST_PORT",
    12: "IPV4_DST_ADDR",
    21: "LAST_SWITCHED",
    22: "FIRST_SWITCHED",
    32: "ICMP_TYPE",
    58: "SRC_VLAN",
}



# Data structures
@dataclass
class NetFlowRecord:
    """Normalised representation of a single flow, no matter version type."""
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int          # 6=TCP, 17=UDP, 1=ICMP
    tcp_flags: int         # bitmask: SYN=0x02, ACK=0x10, FIN=0x01, RST=0x04
    packets: int
    bytes_: int
    start_ms: int          # milliseconds since router boot
    end_ms: int
    timestamp: float       # Unix epoch (set by collector at receipt time)

    @property
    def duration_sec(self) -> float:
        delta = self.end_ms - self.start_ms
        if delta < 0:
            delta += 2**32          # handle counter wrap
        return delta / 1000.0

    def to_csv_row(self) -> dict:
        return {
            "timestamp":   self.timestamp,
            "src_ip":      self.src_ip,
            "dst_ip":      self.dst_ip,
            "src_port":    self.src_port,
            "dst_port":    self.dst_port,
            "protocol":    self.protocol,
            "tcp_flags":   self.tcp_flags,
            "packets":     self.packets,
            "bytes":       self.bytes_,
            "duration_sec": self.duration_sec,
        }



# NetFlow v5 parser
def parse_netflow_v5(data: bytes, recv_time: float) -> List[NetFlowRecord]:

    if len(data) < NF5_HEADER_SIZE:
        return []

    # Header: version(2), count(2), sys_uptime(4), unix_secs(4),
    version, count = struct.unpack_from("!HH", data, 0)
    if version != 5:
        return []

    records = []
    offset = NF5_HEADER_SIZE
    for _ in range(count):
        if offset + NF5_RECORD_SIZE > len(data):
            break

        (src_ip_raw, dst_ip_raw, _nexthop,
         _in_if, _out_if,
         packets, bytes_,
         first_ms, last_ms,
         src_port, dst_port,
         _pad, tcp_flags, protocol,
         _tos, _src_as, _dst_as,
         _src_mask, _dst_mask, _pad2) = struct.unpack_from(
            "!4s4s4sHHIIIIHHBBBBHHBBH", data, offset
        )

        records.append(NetFlowRecord(
            src_ip=socket.inet_ntoa(src_ip_raw),
            dst_ip=socket.inet_ntoa(dst_ip_raw),
            src_port=src_port,
            dst_port=dst_port,
            protocol=protocol,
            tcp_flags=tcp_flags,
            packets=packets,
            bytes_=bytes_,
            start_ms=first_ms,
            end_ms=last_ms,
            timestamp=recv_time,
        ))
        offset += NF5_RECORD_SIZE

    return records



# NetFlow v9 parser
def parse_netflow_v9(data: bytes, recv_time: float, source_addr: str = "default") -> List[NetFlowRecord]:

    if len(data) < 20:
        return []

    version, count, _uptime, _epoch, _seq, _src_id = struct.unpack_from(
        "!HHIIII", data, 0
    )
    if version != 9:
        return []

    records = []
    offset = 20

    while offset + 4 <= len(data):
        flowset_id, flowset_len = struct.unpack_from("!HH", data, offset)
        if flowset_len < 4:
            break

        payload = data[offset + 4: offset + flowset_len]

        if flowset_id == 0:
            # Template FlowSet — parse and cache
            _parse_nf9_template(payload, source_addr)
        elif flowset_id == 1:
            pass  # Options template — skip
        elif flowset_id >= 256:
            # Data FlowSet
            recs = _decode_nf9_data(flowset_id, payload, recv_time, source_addr)
            records.extend(recs)

        offset += flowset_len
        # align to 4-byte boundary
        if flowset_len % 4:
            offset += 4 - (flowset_len % 4)

    return records


# Module-level template cache: (source_addr, template_id) - list of (field_type, field_len)
_NF9_TEMPLATES: dict = {}


def _parse_nf9_template(payload: bytes, source_addr: str = "default") -> None:
    pos = 0
    while pos + 4 <= len(payload):
        tmpl_id, field_count = struct.unpack_from("!HH", payload, pos)
        pos += 4
        fields = []
        for _ in range(field_count):
            if pos + 4 > len(payload):
                break
            ftype, flen = struct.unpack_from("!HH", payload, pos)
            fields.append((ftype, flen))
            pos += 4
        _NF9_TEMPLATES[(source_addr, tmpl_id)] = fields


def _decode_nf9_data(
    template_id: int, payload: bytes, recv_time: float, source_addr: str = "default"
) -> List[NetFlowRecord]:
    key = (source_addr, template_id)
    if key not in _NF9_TEMPLATES:
        return []   # template not yet received from this exporter

    fields = _NF9_TEMPLATES[key]
    record_len = sum(f[1] for f in fields)
    if record_len == 0:
        return []

    records = []
    pos = 0
    while pos + record_len <= len(payload):
        values: dict = {}
        for ftype, flen in fields:
            raw = payload[pos: pos + flen]
            pos += flen
            fname = NF9_FIELD_TYPES.get(ftype)
            if fname is None:
                continue
            if fname in ("IPV4_SRC_ADDR", "IPV4_DST_ADDR"):
                values[fname] = socket.inet_ntoa(raw.ljust(4, b"\x00")[:4])
            else:
                values[fname] = int.from_bytes(raw, "big")

        rec = NetFlowRecord(
            src_ip=values.get("IPV4_SRC_ADDR", "0.0.0.0"),
            dst_ip=values.get("IPV4_DST_ADDR", "0.0.0.0"),
            src_port=values.get("L4_SRC_PORT", 0),
            dst_port=values.get("L4_DST_PORT", 0),
            protocol=values.get("PROTOCOL", 0),
            tcp_flags=values.get("TCP_FLAGS", 0),
            packets=values.get("IN_PKTS", 0),
            bytes_=values.get("IN_BYTES", 0),
            start_ms=values.get("FIRST_SWITCHED", 0),
            end_ms=values.get("LAST_SWITCHED", 0),
            timestamp=recv_time,
        )
        records.append(rec)

    return records



# pcap parser (uses scapy when available, falls back to dpkt)
def parse_pcap_file(path: str, recv_time_override: Optional[float] = None):
    import time

    try:
        from scapy.all import PcapReader, UDP, IP, Raw
        _parse_pcap = _parse_pcap_scapy
    except ImportError:
        try:
            import dpkt
            _parse_pcap = _parse_pcap_dpkt
        except ImportError:
            raise RuntimeError(
                "Install scapy or dpkt to read pcap files: "
            )

    yield from _parse_pcap(path, recv_time_override)


NETFLOW_PORTS = {2055, 9995, 9996}


def _parse_pcap_scapy(path: str, recv_time_override):
    from scapy.all import PcapReader, UDP, IP, Raw
    import time

    with PcapReader(path) as pcap:
        for pkt in pcap:
            t = recv_time_override or float(pkt.time)
            if pkt.haslayer(UDP) and pkt.haslayer(IP):
                dport = pkt[UDP].dport
                if dport in NETFLOW_PORTS and pkt.haslayer(Raw):
                    raw = bytes(pkt[Raw])
                    version = struct.unpack_from("!H", raw, 0)[0] if len(raw) >= 2 else 0
                    exporter_ip = pkt[IP].src
                    if version == 5:
                        yield from parse_netflow_v5(raw, t)
                    elif version == 9:
                        yield from parse_netflow_v9(raw, t, source_addr=exporter_ip)
                elif pkt.haslayer(IP):
                    # Treat each IP packet as a synthetic flow record
                    yield _packet_to_record(pkt, t)


def _parse_pcap_dpkt(path: str, recv_time_override):
    import dpkt, socket as _socket

    with open(path, "rb") as f:
        try:
            pcap = dpkt.pcap.Reader(f)
        except Exception:
            pcap = dpkt.pcapng.Reader(f)

        for ts, buf in pcap:
            t = recv_time_override or ts
            try:
                eth = dpkt.ethernet.Ethernet(buf)
                ip = eth.data
                if not isinstance(ip, dpkt.ip.IP):
                    continue

                proto = ip.p
                src = _socket.inet_ntoa(ip.src)
                dst = _socket.inet_ntoa(ip.dst)

                if isinstance(ip.data, (dpkt.udp.UDP, dpkt.tcp.TCP)):
                    transport = ip.data
                    sport = transport.sport
                    dport = transport.dport
                    data_bytes = bytes(transport.data) if hasattr(transport, "data") else b""

                    # Check for NetFlow exports
                    if proto == 17 and dport in NETFLOW_PORTS and data_bytes:
                        version = struct.unpack_from("!H", data_bytes, 0)[0] if len(data_bytes) >= 2 else 0
                        if version == 5:
                            yield from parse_netflow_v5(data_bytes, t)
                            continue
                        elif version == 9:
                            yield from parse_netflow_v9(data_bytes, t)
                            continue

                    flags = getattr(transport, "flags", 0) if proto == 6 else 0
                    pkt_len = len(buf)
                    yield NetFlowRecord(
                        src_ip=src, dst_ip=dst,
                        src_port=sport, dst_port=dport,
                        protocol=proto, tcp_flags=flags,
                        packets=1, bytes_=pkt_len,
                        start_ms=int(t * 1000), end_ms=int(t * 1000),
                        timestamp=t,
                    )
                else:
                    # ICMP or other
                    yield NetFlowRecord(
                        src_ip=src, dst_ip=dst,
                        src_port=0, dst_port=0,
                        protocol=proto, tcp_flags=0,
                        packets=1, bytes_=len(buf),
                        start_ms=int(t * 1000), end_ms=int(t * 1000),
                        timestamp=t,
                    )
            except Exception:
                continue


def _packet_to_record(pkt, t: float) -> NetFlowRecord:
    """Convert a single scapy packet to a NetFlowRecord (synthetic flow)."""
    from scapy.all import IP, TCP, UDP, ICMP
    ip = pkt[IP] if pkt.haslayer(IP) else None
    if ip is None:
        return None
    proto = ip.proto
    sport = dport = flags = 0
    if pkt.haslayer(TCP):
        sport, dport = pkt[TCP].sport, pkt[TCP].dport
        flags = pkt[TCP].flags
    elif pkt.haslayer(UDP):
        sport, dport = pkt[UDP].sport, pkt[UDP].dport

    return NetFlowRecord(
        src_ip=ip.src, dst_ip=ip.dst,
        src_port=sport, dst_port=dport,
        protocol=proto, tcp_flags=int(flags),
        packets=1, bytes_=len(pkt),
        start_ms=int(t * 1000), end_ms=int(t * 1000),
        timestamp=t,
    )
