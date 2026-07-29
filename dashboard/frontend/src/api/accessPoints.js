import { api } from './client'

export async function getAccessPoints() {
  const data = await api.get('/access-points')
  return data.access_points || []
}
