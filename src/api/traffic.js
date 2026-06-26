import { api, USE_MOCK } from './client'
import { generateTrafficRecent, mockLiveScores } from './mock/mockTraffic'

export const getTrafficRecent = (minutes = 15) =>
  USE_MOCK ? Promise.resolve(generateTrafficRecent(minutes)) : api.get(`/traffic/recent?minutes=${minutes}`)

export const getLiveScores = (minutes = 1) =>
  USE_MOCK ? Promise.resolve(mockLiveScores) : api.get(`/traffic/live-scores?minutes=${minutes}`)
