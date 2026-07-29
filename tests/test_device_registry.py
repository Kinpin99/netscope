import yaml

from utils.device_registry import DeviceRegistry


def test_device_registry_crud_and_topology_update(tmp_path):
    config_path = tmp_path / "config.yaml"
    config = {
        "devices": [
            {
                "ip": "10.0.0.1",
                "name": "core-router",
                "building": "HQ",
                "sensors": {},
            }
        ],
        "topology": {
            "links": [
                {"source": "core-router", "target": "edge-switch", "type": "uplink"}
            ]
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    registry = DeviceRegistry(str(config_path))

    created = registry.create({
        "ip": "10.0.0.2",
        "name": "edge-switch",
        "building": "HQ",
        "device_type": "switch",
        "snmp": {
            "enabled": True,
            "host": "10.0.0.2",
            "community": "public",
            "port": 161,
            "if_index": 2,
            "if_speed_bps": 1000000000,
            "output_ip": "10.0.0.2",
        },
    })
    assert created["snmp"]["enabled"] is True
    assert len(registry.list_devices()) == 2

    updated = registry.update("10.0.0.2", {
        **created,
        "ip": "10.0.0.20",
        "name": "edge-switch-floor2",
        "snmp": created["snmp"],
    })
    assert updated["ip"] == "10.0.0.20"

    raw = yaml.safe_load(config_path.read_text())
    assert raw["topology"]["links"][0]["target"] == "edge-switch-floor2"

    deleted = registry.delete("10.0.0.20")
    assert deleted["name"] == "edge-switch-floor2"
    raw = yaml.safe_load(config_path.read_text())
    assert len(raw["devices"]) == 1
    assert raw["topology"]["links"] == []
