import { USE_MOCK } from './client'
import { mockLogin, mockGetMe, mockRegister } from './mock/mockAuth'

export async function login(username, password) {
  if (USE_MOCK) {
    await new Promise(r => setTimeout(r, 400))
    return mockLogin(username, password)
  }

  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    return { ok: false, error: body.detail || 'Login failed.' }
  }

  return { ok: true, data: await res.json() }
}

export async function register(name, username, password, role) {
  if (USE_MOCK) {
    await new Promise(r => setTimeout(r, 400))
    return mockRegister(name, username, password, role)
  }

  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, username, password, role }),
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    return { ok: false, error: body.detail || 'Registration failed.' }
  }

  return { ok: true, data: await res.json() }
}

export async function getMe(token) {
  if (USE_MOCK) return mockGetMe(token)

  const res = await fetch('/api/auth/me', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) return null
  return res.json()
}
