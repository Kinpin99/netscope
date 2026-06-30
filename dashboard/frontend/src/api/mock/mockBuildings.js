export const mockBuildings = [
  {
    building: 'Block A',
    device_count: 4,
    open_issue_count: 2,
    max_severity: 'high',
    avg_health_score: 74.5,
    devices: [
      { ip: '10.0.0.1', name: 'core-router-01', device_type: 'router', status: 'online', health_score: 92 },
      { ip: '10.0.0.2', name: 'dist-sw-01', device_type: 'switch', status: 'online', health_score: 79 },
      { ip: '10.0.0.3', name: 'dist-sw-02', device_type: 'switch', status: 'degraded', health_score: 54 },
      { ip: '10.0.0.4', name: 'ap-block-a-01', device_type: 'access_point', status: 'online', health_score: 73 },
    ],
  },
  {
    building: 'Block B',
    device_count: 4,
    open_issue_count: 3,
    max_severity: 'critical',
    avg_health_score: 58.0,
    devices: [
      { ip: '10.0.1.1', name: 'dist-sw-03', device_type: 'switch', status: 'online', health_score: 81 },
      { ip: '10.0.1.2', name: 'dist-sw-04', device_type: 'switch', status: 'degraded', health_score: 47 },
      { ip: '10.0.1.3', name: 'ap-block-b-01', device_type: 'access_point', status: 'online', health_score: 66 },
      { ip: '10.0.1.4', name: 'ap-block-b-02', device_type: 'access_point', status: 'offline', health_score: 22 },
    ],
  },
  {
    building: 'Engineering Lab',
    device_count: 3,
    open_issue_count: 1,
    max_severity: 'medium',
    avg_health_score: 82.3,
    devices: [
      { ip: '10.0.2.1', name: 'lab-sw-01', device_type: 'switch', status: 'online', health_score: 88 },
      { ip: '10.0.2.2', name: 'lab-host-01', device_type: 'host', status: 'online', health_score: 91 },
      { ip: '10.0.2.3', name: 'lab-host-02', device_type: 'host', status: 'unknown', health_score: null },
    ],
  },
]
