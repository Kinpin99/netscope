#!/usr/bin/env python3
"""
ztp_device_agent.py
-------------------
Python 3.5-compatible simulated ZTP/PnP phone-home agent.

Run it from the Mininet VM shell or from a Mininet host namespace if the host
can reach the controller URL. It phones home, waits for admin approval, downloads
the generated config, saves it locally, and marks provisioning complete.

Example:
  python3 simulation/ztp_device_agent.py \
    --controller http://192.168.23.1:8000 \
    --serial AP-LIB-001 \
    --mac 02:00:00:00:07:00 \
    --device-type access_point \
    --model Simulated-AP \
    --management-ip 10.255.0.31 \
    --data-ip 10.0.1.21
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import time

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:  # pragma: no cover - Python 2 fallback if needed
    from urllib2 import Request, urlopen, HTTPError, URLError


def post_json(url, payload):
    body = json.dumps(payload).encode('utf-8')
    req = Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        resp = urlopen(req, timeout=10)
        raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            detail = exc.read().decode('utf-8')
        except Exception:
            detail = str(exc)
        raise RuntimeError('HTTP {0}: {1}'.format(exc.code, detail))
    except URLError as exc:
        raise RuntimeError('Connection failed: {0}'.format(exc))


def main():
    parser = argparse.ArgumentParser(description='Simulated ZTP/PnP device phone-home agent')
    parser.add_argument('--controller', required=True, help='Controller base URL, e.g. http://192.168.23.1:8000')
    parser.add_argument('--serial', required=True)
    parser.add_argument('--mac', required=True)
    parser.add_argument('--device-type', default='access_point')
    parser.add_argument('--model', default='Simulated-AP')
    parser.add_argument('--firmware-version', default='1.0')
    parser.add_argument('--management-ip', required=True)
    parser.add_argument('--data-ip', default='')
    parser.add_argument('--hostname', default='')
    parser.add_argument('--enrollment-token', default=os.environ.get('NETSCOPE_ONBOARDING_SECRET', ''))
    parser.add_argument('--poll-interval', type=int, default=5)
    parser.add_argument('--max-wait', type=int, default=300)
    parser.add_argument('--output', default='data/onboarding/applied_device_config.conf')
    args = parser.parse_args()

    controller = args.controller.rstrip('/')
    payload = {
        'serial_number': args.serial,
        'mac_address': args.mac,
        'device_type': args.device_type,
        'model': args.model,
        'firmware_version': args.firmware_version,
        'management_ip': args.management_ip,
        'data_ip': args.data_ip or None,
        'hostname': args.hostname or None,
        'enrollment_token': args.enrollment_token or None,
        'capabilities': {
            'ztp_lite': True,
            'snmp': True,
            'simulated': True,
        },
    }

    start = time.time()
    device_id = None
    print('[ztp] contacting controller {0}'.format(controller))
    while True:
        result = post_json(controller + '/onboarding/phone-home', payload)
        device = result.get('device') or {}
        device_id = device.get('id') or device_id
        action = result.get('action')
        status = device.get('status')
        print('[ztp] device={0} status={1} action={2}'.format(device_id, status, action))

        if action == 'download_config':
            config = result.get('config') or {}
            text = config.get('config_text') or json.dumps(config.get('config') or {}, indent=2)
            out_path = args.output
            parent = os.path.dirname(out_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent)
            with open(out_path, 'w') as handle:
                handle.write(text)
            print('[ztp] saved generated config to {0}'.format(out_path))
            post_json(controller + '/onboarding/devices/{0}/complete'.format(device_id), {
                'status': 'success',
                'message': 'Simulated device applied generated config',
            })
            print('[ztp] provisioning complete')
            return 0

        if action == 'rejected':
            print('[ztp] onboarding rejected by controller')
            return 2

        if time.time() - start > args.max_wait:
            print('[ztp] timed out waiting for admin approval')
            return 1
        time.sleep(args.poll_interval)


if __name__ == '__main__':
    sys.exit(main())
