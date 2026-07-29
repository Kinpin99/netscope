import { api, USE_MOCK } from './client'
import { mockBuildings } from './mock/mockBuildings'
import { mockDevicesList } from './mock/mockDevices'

export const getBuildings = () =>
  USE_MOCK ? Promise.resolve(mockBuildings) : api.get('/topology/buildings').then(r => r.buildings || r)

export const getDevices = () =>
  USE_MOCK ? Promise.resolve(mockDevicesList) : api.get('/topology/devices').then(r => r.devices || r)
