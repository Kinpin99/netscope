export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const BASE = '/api'

async function request(path, options = {}) {
  const token = localStorage.getItem('netscope_token')
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { headers, ...options })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  get:    (path)        => request(path),
  post:   (path, body)  => request(path, { method: 'POST', body: JSON.stringify(body) }),
  delete: (path)        => request(path, { method: 'DELETE' }),
}
