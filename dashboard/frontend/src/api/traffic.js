import { api, USE_MOCK } from './client'
import { generateTrafficRecent, mockLiveScores } from './mock/mockTraffic'

export const getTrafficRecent = (minutes = 15, maxDevices = 48) =>
  USE_MOCK
    ? Promise.resolve(generateTrafficRecent(minutes))
    : api.get(`/traffic/recent?minutes=${minutes}&max_devices=${maxDevices}`).then(r => ({
        window_sec: r?.window_sec || 60,
        network: r?.network || [],
        device_count: r?.device_count || Object.keys(r?.devices || {}).length,
        devices: r?.devices || {},
      }))

export const getDeviceTraffic = (ip, minutes = 60) =>
  USE_MOCK
    ? Promise.resolve(generateTrafficRecent(minutes)?.devices?.[ip] || [])
    : api.get(`/traffic/device/${encodeURIComponent(ip)}?minutes=${minutes}`).then(r => r?.series || [])

export const getLiveScores = (minutes = 3) =>
  USE_MOCK
    ? Promise.resolve(mockLiveScores)
    : api.get(`/traffic/live-scores?minutes=${minutes}`).then(r => r?.scores || [])
