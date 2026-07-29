import time
from pathlib import Path

import yaml

from troubleshooting.syslog_parser import parse_syslog_line
from troubleshooting.event_store import SyslogEventStore
from troubleshooting.root_cause_engine import RootCauseEngine


def test_syslog_parser_classifies_interface_down():
    event = parse_syslog_line('<189>Jun 27 12:03:44 core-router-01 %LINK-3-UPDOWN: Interface eth1 changed state to down')
    assert event['event_type'] == 'interface_down'
    assert event['severity'] == 'high'
    assert event['device_name'] == 'core-router-01'
    assert event['interface'] == 'eth1'


def test_root_cause_engine_builds_incident(tmp_path, monkeypatch):
    root = tmp_path
    (root / 'data' / 'alerts').mkdir(parents=True)
    (root / 'data' / 'models').mkdir(parents=True)
    (root / 'data' / 'raw').mkdir(parents=True)
    cfg = {
        'devices': [
            {'ip': '10.0.0.5', 'name': 'core-router-01', 'building': 'HQ', 'sensors': {}},
            {'ip': '10.0.0.6', 'name': 'edge-switch-floor2', 'building': 'HQ', 'sensors': {}},
        ],
        'paths': {
            'models_dir': str(root / 'data' / 'models'),
            'alerts_dir': str(root / 'data' / 'alerts'),
            'prtg_raw_dir': str(root / 'data' / 'raw'),
        },
        'topology': {'links': [{'source': 'core-router-01', 'target': 'edge-switch-floor2'}]},
        'troubleshooting': {'syslog_events_file': str(root / 'data' / 'syslogs' / 'events.jsonl')},
    }
    config_path = root / 'config.yaml'
    config_path.write_text(yaml.safe_dump(cfg))

    store = SyslogEventStore(path=root / 'data' / 'syslogs' / 'events.jsonl')
    store.append_raw('<189>Jun 27 12:03:44 core-router-01 %LINK-3-UPDOWN: Interface eth1 changed state to down')

    # Monkeypatch load_config by passing the config path to RootCauseEngine.
    result = RootCauseEngine(config_path=str(config_path)).analyze(last_hours=168)
    assert result['incidents']
    assert result['incidents'][0]['root_cause_type'] == 'interface_down'
