import yaml
from fastapi.testclient import TestClient


def test_settings_device_crud(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config = {
        "devices": [],
        "paths": {
            "netflow_raw_dir": str(tmp_path / "raw"),
            "prtg_raw_dir": str(tmp_path / "raw"),
            "processed_dir": str(tmp_path / "processed"),
            "models_dir": str(tmp_path / "models"),
            "alerts_dir": str(tmp_path / "alerts"),
        },
        "security": {
            "users_file": str(tmp_path / "users.json"),
            "audit_log_path": str(tmp_path / "audit.log"),
            "enforce_ip_allowlist": False,
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    import utils.config_loader as config_loader
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setenv("NETSCOPE_JWT_SECRET", "settings-test-secret")
    monkeypatch.setenv("NETSCOPE_BOOTSTRAP_USERNAME", "admin")
    monkeypatch.setenv("NETSCOPE_BOOTSTRAP_PASSWORD", "AdminPass123!")

    from api.main import app
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin", "password": "AdminPass123!"})
    assert login.status_code == 200
    client.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})

    payload = {
        "ip": "10.10.0.1",
        "name": "test-router",
        "building": "Lab",
        "device_type": "router",
        "snmp": {
            "enabled": True,
            "host": "10.10.0.1",
            "community": "public",
            "port": 161,
            "if_index": 1,
            "if_speed_bps": 100000000,
            "output_ip": "10.10.0.1",
        },
    }
    created = client.post("/settings/devices", json=payload)
    assert created.status_code == 201
    assert created.json()["device"]["name"] == "test-router"

    listed = client.get("/settings/devices")
    assert listed.status_code == 200
    assert len(listed.json()["devices"]) == 1

    payload["name"] = "test-router-updated"
    updated = client.put("/settings/devices/10.10.0.1", json=payload)
    assert updated.status_code == 200
    assert updated.json()["device"]["name"] == "test-router-updated"

    deleted = client.delete("/settings/devices/10.10.0.1")
    assert deleted.status_code == 200
    assert client.get("/settings/devices").json()["devices"] == []
