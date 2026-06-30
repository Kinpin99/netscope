export const mockDevicesList = [
  { ip: '10.0.0.1', name: 'core-router-01', building: 'Block A', device_type: 'router', status: 'online', health_score: 92 },
  { ip: '10.0.0.2', name: 'dist-sw-01', building: 'Block A', device_type: 'switch', status: 'online', health_score: 79 },
  { ip: '10.0.0.3', name: 'dist-sw-02', building: 'Block A', device_type: 'switch', status: 'degraded', health_score: 54 },
  { ip: '10.0.0.4', name: 'ap-block-a-01', building: 'Block A', device_type: 'access_point', status: 'online', health_score: 73 },
  { ip: '10.0.1.1', name: 'dist-sw-03', building: 'Block B', device_type: 'switch', status: 'online', health_score: 81 },
  { ip: '10.0.1.2', name: 'dist-sw-04', building: 'Block B', device_type: 'switch', status: 'degraded', health_score: 47 },
  { ip: '10.0.1.3', name: 'ap-block-b-01', building: 'Block B', device_type: 'access_point', status: 'online', health_score: 66 },
  { ip: '10.0.1.4', name: 'ap-block-b-02', building: 'Block B', device_type: 'access_point', status: 'offline', health_score: 22 },
  { ip: '10.0.2.1', name: 'lab-sw-01', building: 'Engineering Lab', device_type: 'switch', status: 'online', health_score: 88 },
  { ip: '10.0.2.2', name: 'lab-host-01', building: 'Engineering Lab', device_type: 'host', status: 'online', health_score: 91 },
  { ip: '10.0.2.3', name: 'lab-host-02', building: 'Engineering Lab', device_type: 'host', status: 'unknown', health_score: null },
]

// single device detail response shape
export const mockDeviceDetail = (ip) => {
  const d = mockDevicesList.find(d => d.ip === ip)
  if (!d) return null
  return {
    ip: d.ip,
    name: d.name,
    building: d.building,
    device_type: d.device_type,
    status: d.status,
    health_score: d.health_score,
    open_alerts: [],
    has_per_device_profile: d.ip === '10.0.0.1' || d.ip === '10.0.1.2',
  }
}
