from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import networkx as nx
except Exception:
    nx = None

from utils.config_loader import load_config


class NetworkTopologyGraph:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        if nx is None:
            raise RuntimeError("NetworkX is required for troubleshooting topology analysis")
        self.graph = nx.DiGraph()
        self.alias_to_node: Dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        for device in self.cfg.get("devices", []):
            node_id = device.get("ip") or device.get("name")
            if not node_id:
                continue
            self.graph.add_node(node_id, **device)
            self._alias(node_id, node_id)
            for key in ("name", "ip", "serial_number", "mac_address"):
                value = device.get(key)
                if value:
                    self._alias(str(value), node_id)

        links = ((self.cfg.get("topology") or {}).get("links") or [])
        for link in links:
            upstream = self.resolve(link.get("source") or link.get("upstream"))
            downstream = self.resolve(link.get("target") or link.get("downstream"))
            if not upstream or not downstream:
                continue
            attrs = {k: v for k, v in link.items() if k not in {"source", "target", "upstream", "downstream"}}
            self.graph.add_edge(upstream, downstream, **attrs)

    def _alias(self, value: str, node_id: str) -> None:
        self.alias_to_node[value] = node_id
        self.alias_to_node[value.lower()] = node_id

    def resolve(self, identifier: Optional[str]) -> Optional[str]:
        if not identifier:
            return None
        value = str(identifier)
        return self.alias_to_node.get(value) or self.alias_to_node.get(value.lower())

    def downstream(self, identifier: str) -> List[str]:
        node = self.resolve(identifier)
        if not node:
            return []
        return sorted(nx.descendants(self.graph, node))

    def upstream(self, identifier: str) -> List[str]:
        node = self.resolve(identifier)
        if not node:
            return []
        return sorted(nx.ancestors(self.graph, node))

    def common_upstream(self, identifiers: Iterable[str]) -> Optional[Dict[str, Any]]:
        nodes = [self.resolve(x) for x in identifiers if self.resolve(x)]
        if not nodes:
            return None
        candidate_sets: List[Set[str]] = []
        for node in nodes:
            candidate_sets.append(set(nx.ancestors(self.graph, node)) | {node})
        common = set.intersection(*candidate_sets) if candidate_sets else set()
        if not common:
            return None

        # Prefer the closest common upstream to the affected nodes.
        def score(candidate: str) -> int:
            total = 0
            for node in nodes:
                try:
                    total += nx.shortest_path_length(self.graph, candidate, node)
                except Exception:
                    total += 999
            return total

        best = sorted(common, key=score)[0]
        return self.node_payload(best)

    def node_payload(self, node_id: str) -> Dict[str, Any]:
        attrs = dict(self.graph.nodes[node_id])
        attrs.setdefault("id", node_id)
        attrs.setdefault("ip", node_id)
        attrs["downstream_count"] = len(nx.descendants(self.graph, node_id))
        attrs["upstream_count"] = len(nx.ancestors(self.graph, node_id))
        return attrs

    def to_json(self) -> Dict[str, Any]:
        nodes = []
        for node_id in self.graph.nodes:
            payload = self.node_payload(node_id)
            nodes.append(payload)
        edges = []
        for source, target, attrs in self.graph.edges(data=True):
            item = {"source": source, "target": target}
            item.update(attrs)
            edges.append(item)
        return {"nodes": nodes, "edges": edges}
