#!/usr/bin/env python3
"""
mininet_school_topology.py
--------------------------
Python 3.5-compatible Mininet topology for the school-network simulation.

This version intentionally uses canonical Mininet switch names (s1, s2, ...)
because older Mininet versions cannot derive datapath IDs from names such as
s_hq or s_branch.

Logical mapping:
  s1 = HQ access switch
  s2 = Branch access switch
  s3 = DMZ/server switch
  s4 = Core/WAN switch
  s5 = SNMP management switch

Host mapping:
  h1 = finance-pc      10.0.1.11/24
  h2 = staff-pc        10.0.1.12/24
  h3 = branch-pc       10.0.2.11/24
  h4 = branch-server   10.0.2.21/24
  h5 = web-server      10.0.3.10/24
  h6 = dns-server      10.0.3.53/24
  h7 = ap-library      10.0.1.21/24, 10.255.0.31/24 (onboarding demo AP)
  h0 = root namespace management interface, 10.255.0.254/24

Typical use inside the Mininet VM:

  sudo python3 simulation/mininet_school_topology.py \
    --netflow-target 192.168.56.1:2055 \
    --config-out simulation/mininet_config.generated.yaml

Then in another Mininet VM terminal:

  python3 collectors/snmp_prtg_collector.py \
    --mode poll \
    --config simulation/mininet_config.generated.yaml \
    --backend cli
"""

import argparse
from pathlib import Path
from textwrap import dedent

try:
    import yaml
    from mininet.cli import CLI
    from mininet.link import TCLink
    from mininet.log import info, setLogLevel, warn
    from mininet.net import Mininet
    from mininet.node import Node, OVSKernelSwitch
except ImportError:
    raise SystemExit(
        "This script must be run inside a Mininet VM with the mininet Python "
        "package installed. It also needs PyYAML. Install missing packages "
        "with: sudo apt-get install -y python3-yaml"
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LinuxRouter(Node):
    """A host namespace with IPv4 forwarding enabled, used as a router."""

    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")
        self.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0")
        self.cmd("sysctl -w net.ipv4.conf.default.rp_filter=0")

    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        super(LinuxRouter, self).terminate()


def _require_commands(net, enable_snmp):
    """Print clear installation hints if common commands are missing."""
    probe = net.get("h1")
    missing = []
    for cmd in ["python3", "ovs-vsctl"]:
        if not probe.cmd("which {0}".format(cmd)).strip():
            missing.append(cmd)
    if enable_snmp and not probe.cmd("which snmpd").strip():
        missing.append("snmpd")
    if missing:
        warn("*** Missing commands: {0}\n".format(", ".join(missing)))
        warn(
            "*** On Ubuntu/Mininet VM, install with: sudo apt-get update && "
            "sudo apt-get install -y snmp snmpd iperf nmap curl python3-yaml\n"
        )


def build_network():
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)

    info("*** Adding routers\n")
    r1 = net.addHost("r1", cls=LinuxRouter)
    r2 = net.addHost("r2", cls=LinuxRouter)

    info("*** Adding switches\n")
    # Canonical switch names keep older Mininet happy.
    s1 = net.addSwitch("s1", dpid="0000000000000001", failMode="standalone")  # HQ access
    s2 = net.addSwitch("s2", dpid="0000000000000002", failMode="standalone")  # Branch access
    s3 = net.addSwitch("s3", dpid="0000000000000003", failMode="standalone")  # DMZ/server
    s4 = net.addSwitch("s4", dpid="0000000000000004", failMode="standalone")  # Core/WAN
    s5 = net.addSwitch("s5", dpid="0000000000000005", failMode="standalone")  # SNMP management

    info("*** Adding hosts\n")
    h1 = net.addHost("h1", ip="10.0.1.11/24", defaultRoute="via 10.0.1.1")
    h2 = net.addHost("h2", ip="10.0.1.12/24", defaultRoute="via 10.0.1.1")
    h3 = net.addHost("h3", ip="10.0.2.11/24", defaultRoute="via 10.0.2.1")
    h4 = net.addHost("h4", ip="10.0.2.21/24", defaultRoute="via 10.0.2.1")
    h5 = net.addHost("h5", ip="10.0.3.10/24", defaultRoute="via 10.0.3.1")
    h6 = net.addHost("h6", ip="10.0.3.53/24", defaultRoute="via 10.0.3.1")
    # Factory-default/simulated access point used for ZTP-lite onboarding demo.
    # It is intentionally not written to the generated monitoring config until
    # approved through the dashboard onboarding workflow.
    h7 = net.addHost("h7", ip="10.0.1.21/24", defaultRoute="via 10.0.1.1")

    info("*** Adding data-plane links\n")
    net.addLink(r1, s1, intfName1="r1-hq", bw=100)
    net.addLink(r1, s4, intfName1="r1-core", bw=100)
    net.addLink(r1, s3, intfName1="r1-dmz", bw=100)

    net.addLink(r2, s2, intfName1="r2-branch", bw=100)
    net.addLink(r2, s4, intfName1="r2-core", bw=100)

    net.addLink(h1, s1, intfName1="h1-eth0", bw=100)
    net.addLink(h2, s1, intfName1="h2-eth0", bw=100)
    net.addLink(h3, s2, intfName1="h3-eth0", bw=100)
    net.addLink(h4, s2, intfName1="h4-eth0", bw=100)
    net.addLink(h5, s3, intfName1="h5-eth0", bw=100)
    net.addLink(h6, s3, intfName1="h6-eth0", bw=100)
    net.addLink(h7, s1, intfName1="h7-eth0", bw=100)

    info("*** Adding SNMP management network links\n")
    net.addLink(r1, s5, intfName1="r1-mgmt", bw=100)
    net.addLink(r2, s5, intfName1="r2-mgmt", bw=100)
    net.addLink(h4, s5, intfName1="h4-mgmt", bw=100)
    net.addLink(h5, s5, intfName1="h5-mgmt", bw=100)
    net.addLink(h6, s5, intfName1="h6-mgmt", bw=100)
    net.addLink(h7, s5, intfName1="h7-mgmt", bw=100)

    # Root namespace node so the Mininet VM itself can poll 10.255.0.0/24.
    h0 = net.addHost("h0", inNamespace=False, ip="10.255.0.254/24")
    net.addLink(h0, s5, intfName1="h0-mgmt", bw=100)

    return net


def configure_ips_and_routes(net):
    info("*** Configuring IP addresses and static routes\n")
    r1 = net.get("r1")
    r2 = net.get("r2")

    r1.cmd("ip addr flush dev r1-hq && ip addr add 10.0.1.1/24 dev r1-hq")
    r1.cmd("ip addr flush dev r1-core && ip addr add 10.0.12.1/30 dev r1-core")
    r1.cmd("ip addr flush dev r1-dmz && ip addr add 10.0.3.1/24 dev r1-dmz")
    r1.cmd("ip addr flush dev r1-mgmt && ip addr add 10.255.0.1/24 dev r1-mgmt")

    r2.cmd("ip addr flush dev r2-branch && ip addr add 10.0.2.1/24 dev r2-branch")
    r2.cmd("ip addr flush dev r2-core && ip addr add 10.0.12.2/30 dev r2-core")
    r2.cmd("ip addr flush dev r2-mgmt && ip addr add 10.255.0.2/24 dev r2-mgmt")

    net.get("h4").cmd("ip addr add 10.255.0.21/24 dev h4-mgmt")
    net.get("h5").cmd("ip addr add 10.255.0.10/24 dev h5-mgmt")
    net.get("h6").cmd("ip addr add 10.255.0.53/24 dev h6-mgmt")
    net.get("h7").cmd("ip addr add 10.255.0.31/24 dev h7-mgmt")

    net.get("h0").cmd("ip addr flush dev h0-mgmt && ip addr add 10.255.0.254/24 dev h0-mgmt")
    net.get("h0").cmd("ip link set h0-mgmt up")

    r1.cmd("ip route add 10.0.2.0/24 via 10.0.12.2 || true")
    r2.cmd("ip route add 10.0.1.0/24 via 10.0.12.1 || true")
    r2.cmd("ip route add 10.0.3.0/24 via 10.0.12.1 || true")


def configure_ovs_netflow(net, target, active_timeout=10, include_mgmt=False):
    if not target:
        return
    info("*** Configuring OVS NetFlow export to {0}\n".format(target))
    switches = ["s1", "s2", "s3", "s4"]
    if include_mgmt:
        switches.append("s5")
    for sw_name in switches:
        sw = net.get(sw_name)
        sw.cmd("ovs-vsctl clear Bridge {0} netflow || true".format(sw_name))
        cmd = (
            "ovs-vsctl -- --id=@nf create NetFlow "
            "targets=\\\"{0}\\\" active_timeout={1} "
            "-- set Bridge {2} netflow=@nf"
        ).format(target, active_timeout, sw_name)
        out = sw.cmd(cmd)
        if out.strip():
            warn("*** NetFlow command output for {0}: {1}\n".format(sw_name, out))


def _write_snmpd_config(node, community, location):
    conf_path = "/tmp/{0}-snmpd.conf".format(node.name)
    conf = dedent("""
        agentAddress udp:161
        rocommunity {community}
        sysLocation {location}
        sysContact final-year-project
        view all included .1
        # Lab-only config. Do not expose this community string outside Mininet.
    """.format(community=community, location=location)).strip()
    node.cmd("cat > {0} <<'EOF'\n{1}\nEOF".format(conf_path, conf))
    return conf_path


def start_snmp_agents(net, community="public"):
    info("*** Starting snmpd inside monitored namespaces\n")
    for name in ["r1", "r2", "h4", "h5", "h6", "h7"]:
        node = net.get(name)
        node.cmd("pkill -f 'snmpd.*{0}-snmpd.conf' || true".format(name))
        conf_path = _write_snmpd_config(node, community, "Mininet {0}".format(name))
        log_path = "/tmp/{0}-snmpd.log".format(name)
        pid_path = "/tmp/{0}-snmpd.pid".format(name)
        node.cmd("snmpd -f -Lo -C -c {0} -p {1} > {2} 2>&1 &".format(conf_path, pid_path, log_path))


def start_demo_services(net):
    info("*** Starting simple demo services on servers\n")
    web = net.get("h5")
    branch_server = net.get("h4")
    dns = net.get("h6")
    ap = net.get("h7")

    web.cmd("mkdir -p /tmp/web-demo && echo 'Mininet school demo web server' > /tmp/web-demo/index.html")
    web.cmd("cd /tmp/web-demo && python3 -m http.server 80 >/tmp/web-server-http.log 2>&1 &")
    web.cmd("iperf -s >/tmp/web-server-iperf.log 2>&1 &")
    branch_server.cmd("iperf -s >/tmp/branch-server-iperf.log 2>&1 &")
    dns.cmd("python3 -m http.server 8053 >/tmp/dns-server-http.log 2>&1 &")
    ap.cmd("python3 -m http.server 8080 >/tmp/ap-library-http.log 2>&1 &")


def _ifindex(node, intf):
    out = node.cmd("cat /sys/class/net/{0}/ifindex 2>/dev/null".format(intf)).strip()
    try:
        return int(out)
    except ValueError:
        warn("*** Could not determine ifIndex for {0}:{1}; defaulting to 1\n".format(node.name, intf))
        return 1


def write_generated_config(net, path, community="public"):
    """
    Write config for collectors/snmp_prtg_collector.py. The SNMP host is a
    management IP, while output_ip is the logical/data IP used by the models.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    devices = [
        {"ip": "10.0.1.1", "name": "hq-core-router-r1", "building": "HQ/Core",
         "snmp_host": "10.255.0.1", "node": "r1", "intf": "r1-dmz"},
        {"ip": "10.0.2.1", "name": "branch-router-r2", "building": "Branch",
         "snmp_host": "10.255.0.2", "node": "r2", "intf": "r2-branch"},
        {"ip": "10.0.3.10", "name": "web-server", "building": "DMZ",
         "snmp_host": "10.255.0.10", "node": "h5", "intf": "h5-eth0"},
        {"ip": "10.0.2.21", "name": "branch-server", "building": "Branch",
         "snmp_host": "10.255.0.21", "node": "h4", "intf": "h4-eth0"},
        {"ip": "10.0.3.53", "name": "dns-server", "building": "DMZ",
         "snmp_host": "10.255.0.53", "node": "h6", "intf": "h6-eth0"},
    ]

    cfg_devices = []
    for dev in devices:
        node = net.get(dev["node"])
        idx = _ifindex(node, dev["intf"])
        cfg_devices.append({
            "ip": dev["ip"],
            "name": dev["name"],
            "building": dev["building"],
            "sensors": {
                "snmp": {
                    "enabled": True,
                    "host": dev["snmp_host"],
                    "community": community,
                    "port": 161,
                    "if_index": idx,
                    "if_speed_bps": 100000000,
                    "output_ip": dev["ip"],
                    "synthetic": {
                        "base_cpu_pct": 8,
                        "base_mem_pct": 38,
                        "cpu_util_weight": 0.75,
                        "mem_util_weight": 0.10,
                        "jitter_pct": 1.5,
                    },
                }
            },
        })

    config = {
        "system": {"mode": "observation", "kafka_bootstrap": "localhost:9092"},
        "prtg": {
            "base_url": "https://prtg.example.local",
            "api_token": "",
            "poll_interval_sec": 10,
            "avg_interval_sec": 10,
            "poll_lag_sec": 0,
        },
        "snmp_prtg": {
            "community": community,
            "poll_interval_sec": 10,
            "timeout_sec": 2,
            "retries": 1,
        },
        "devices": cfg_devices,
        "bootstrap": {
            "min_collection_days": 0,
            "min_netflow_records": 1000,
            "training_hour_utc": 2,
            "retrain_interval_days": 7,
            "rolling_training_window_days": 90,
        },
        "paths": {
            "netflow_raw_dir": "data/raw",
            "prtg_raw_dir": "data/raw",
            "processed_dir": "data/processed",
            "models_dir": "data/models",
            "alerts_dir": "data/alerts",
        },
        "topology": {
            "links": [
                {"source": "hq-core-router-r1", "target": "branch-router-r2", "type": "wan", "interface": "r1-r2"},
                {"source": "hq-core-router-r1", "target": "web-server", "type": "dmz", "interface": "r1-dmz"},
                {"source": "hq-core-router-r1", "target": "dns-server", "type": "dmz", "interface": "r1-dmz"},
                {"source": "branch-router-r2", "target": "branch-server", "type": "lan", "interface": "r2-branch"},
            ]
        },
        "troubleshooting": {
            "syslog_events_file": "data/syslogs/syslog_events.jsonl",
            "default_analysis_window_hours": 24,
            "incident_confidence_threshold": 40,
        },
    }
    with open(str(path), "w") as handle:
        # PyYAML 3.11 on Ubuntu Xenial does not support sort_keys=.
        yaml.safe_dump(config, handle, default_flow_style=False)
    info("*** Wrote collector config: {0}\n".format(path))


def print_demo_help(config_out, netflow_target):
    info("\n*** Simulation ready\n")
    info("*** Node names used in the Mininet CLI:\n")
    info("    h1=finance-pc, h2=staff-pc, h3=branch-pc, h4=branch-server, h5=web-server, h6=dns-server, h7=ap-library\n")
    info("*** Useful commands from inside the Mininet CLI:\n")
    info("    h1 ping -c 3 10.0.3.10\n")
    info("    h2 curl http://10.0.3.10/\n")
    info("    h1 iperf -c 10.0.3.10 -t 30\n")
    info("    h2 nmap -p 1-200 10.0.3.10\n")
    info("    h1 iperf -u -c 10.0.3.10 -p 53 -b 5M -t 30\n")
    info("    h7 ping -c 3 10.0.3.10\n")
    info("\n*** In another Mininet VM terminal, run SNMP-backed PRTG emulation:\n")
    info("    python3 collectors/snmp_prtg_collector.py --mode poll --config {0} --backend cli\n".format(config_out))
    info("\n*** ZTP-lite onboarding demo device:\n")
    info("    h7 is a simulated factory-default access point: serial AP-LIB-001, mgmt 10.255.0.31, data 10.0.1.21\n")
    info("    From the Mininet VM shell, run the phone-home agent with your host/controller IP, for example:\n")
    info("    python3 simulation/ztp_device_agent.py --controller http://192.168.23.1:8000 --serial AP-LIB-001 --mac 02:00:00:00:07:00 --device-type access_point --model Simulated-AP --management-ip 10.255.0.31 --data-ip 10.0.1.21\n")

    if netflow_target:
        info("\n*** On the host OS, receive NetFlow from OVS:\n")
        info("    python collectors/netflow_collector.py --mode udp --host 0.0.0.0 --port 2055\n")
        info("    OVS target configured as: {0}\n".format(netflow_target))
    info("\n")


def main():
    parser = argparse.ArgumentParser(description="School-style Mininet topology for anomaly detection simulation")
    parser.add_argument("--netflow-target", default="", help="Host collector target, e.g. 192.168.56.1:2055")
    parser.add_argument("--netflow-active-timeout", type=int, default=10)
    parser.add_argument("--include-mgmt-netflow", action="store_true", help="Also export NetFlow from s5")
    parser.add_argument("--no-snmp", action="store_true", help="Do not start snmpd agents")
    parser.add_argument("--community", default="public", help="SNMP v2c community for the lab only")
    parser.add_argument("--config-out", default=str(PROJECT_ROOT / "simulation" / "mininet_config.generated.yaml"))
    parser.add_argument("--no-services", action="store_true", help="Do not start HTTP/iperf demo services")
    args = parser.parse_args()

    setLogLevel("info")
    net = build_network()

    try:
        info("*** Starting network\n")
        net.start()
        _require_commands(net, enable_snmp=(not args.no_snmp))
        configure_ips_and_routes(net)
        configure_ovs_netflow(
            net,
            args.netflow_target,
            active_timeout=args.netflow_active_timeout,
            include_mgmt=args.include_mgmt_netflow,
        )
        if not args.no_snmp:
            start_snmp_agents(net, community=args.community)
        if not args.no_services:
            start_demo_services(net)

        config_out = Path(args.config_out)
        write_generated_config(net, config_out, community=args.community)
        print_demo_help(config_out, args.netflow_target)
        CLI(net)
    finally:
        info("*** Stopping network\n")
        net.stop()


if __name__ == "__main__":
    main()
