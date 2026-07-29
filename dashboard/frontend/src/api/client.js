export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
export const ALLOW_SIGNUP = USE_MOCK || import.meta.env.VITE_ALLOW_SIGNUP === 'true'

const BASE = '/api'
const getCache = new Map()
const inFlightGets = new Map()
const GET_TTL_MS = 1500

async function request(path, options = {}) {
  const token = localStorage.getItem('netscope_token')
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (res.status === 401) window.dispatchEvent(new Event('netscope:session-expired'))
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

function clearGetCache() {
  getCache.clear()
}

function get(path) {
  const cached = getCache.get(path)
  if (cached && Date.now() - cached.at < GET_TTL_MS) return Promise.resolve(cached.data)
  if (inFlightGets.has(path)) return inFlightGets.get(path)

  const promise = request(path)
    .then(data => {
      getCache.set(path, { at: Date.now(), data })
      return data
    })
    .finally(() => inFlightGets.delete(path))

  inFlightGets.set(path, promise)
  return promise
}

async function mutate(path, method, body) {
  const result = await request(path, {
    method,
    ...(body !== undefined ? { body: JSON.stringify(body || {}) } : {}),
  })
  clearGetCache()
  return result
}

export const api = {
  get,
  post: (path, body) => mutate(path, 'POST', body),
  put: (path, body) => mutate(path, 'PUT', body),
  delete: (path) => mutate(path, 'DELETE'),
}
