const USERS = [
  { id: 1, username: 'admin', password: 'NetScope@2024', name: 'Edward Sackey', role: 'NOC Admin' },
  { id: 2, username: 'engineer', password: 'NocEng@2024', name: 'NOC Engineer', role: 'Engineer' },
]

let nextId = 3

export function mockLogin(username, password) {
  const user = USERS.find(u => u.username === username && u.password === password)
  if (!user) {
    return { ok: false, error: 'Invalid username or password.' }
  }

  const token = btoa(JSON.stringify({ sub: user.id, username: user.username }))
  return {
    ok: true,
    data: {
      access_token: token,
      user: { id: user.id, username: user.username, name: user.name, role: user.role },
    },
  }
}

export function mockRegister(name, username, password, role) {
  if (USERS.find(u => u.username === username)) {
    return { ok: false, error: 'Username already taken.' }
  }

  const user = { id: nextId++, username, password, name, role }
  USERS.push(user)

  const token = btoa(JSON.stringify({ sub: user.id, username: user.username }))
  return {
    ok: true,
    data: {
      access_token: token,
      user: { id: user.id, username: user.username, name: user.name, role: user.role },
    },
  }
}

export function mockGetMe(token) {
  try {
    const payload = JSON.parse(atob(token))
    const user = USERS.find(u => u.id === payload.sub)
    if (!user) return null
    return { id: user.id, username: user.username, name: user.name, role: user.role }
  } catch {
    return null
  }
}
