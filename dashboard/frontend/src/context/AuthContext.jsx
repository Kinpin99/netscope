import { createContext, useContext, useReducer, useEffect, useCallback } from 'react'
import { login as apiLogin, register as apiRegister, getMe } from '../api/auth'

const AuthContext = createContext(null)

const TOKEN_KEY = 'netscope_token'
const USER_KEY = 'netscope_user'

const initialState = { user: null, token: null, loading: true, error: null }

function reducer(state, action) {
  switch (action.type) {
    case 'RESTORE':
      return { ...state, user: action.user, token: action.token, loading: false }
    case 'LOGIN_START':
      return { ...state, loading: true, error: null }
    case 'LOGIN_SUCCESS':
      return { user: action.user, token: action.token, loading: false, error: null }
    case 'LOGIN_FAIL':
      return { ...state, loading: false, error: action.error }
    case 'LOGOUT':
      return { ...initialState, loading: false }
    default:
      return state
  }
}

export function AuthProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    dispatch({ type: 'LOGOUT' })
  }, [])

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      dispatch({ type: 'LOGOUT' })
      return
    }

    let cancelled = false
    ;(async () => {
      try {
        const freshUser = await getMe(token)
        if (cancelled) return
        if (freshUser) {
          localStorage.setItem(USER_KEY, JSON.stringify(freshUser))
          dispatch({ type: 'RESTORE', user: freshUser, token })
        } else {
          logout()
        }
      } catch {
        if (!cancelled) logout()
      }
    })()
    return () => { cancelled = true }
  }, [logout])

  useEffect(() => {
    const onExpired = () => logout()
    window.addEventListener('netscope:session-expired', onExpired)
    return () => window.removeEventListener('netscope:session-expired', onExpired)
  }, [logout])

  useEffect(() => {
    if (!state.user) return
    let timer
    const reset = () => {
      clearTimeout(timer)
      timer = setTimeout(logout, 30 * 60 * 1000)
    }
    const events = ['click', 'keydown', 'mousemove', 'scroll', 'touchstart']
    events.forEach(e => window.addEventListener(e, reset, { passive: true }))
    reset()
    return () => {
      clearTimeout(timer)
      events.forEach(e => window.removeEventListener(e, reset))
    }
  }, [state.user, logout])

  const login = useCallback(async (username, password) => {
    dispatch({ type: 'LOGIN_START' })
    const result = await apiLogin(username, password)

    if (!result.ok) {
      dispatch({ type: 'LOGIN_FAIL', error: result.error })
      return false
    }

    const { access_token, user } = result.data
    localStorage.setItem(TOKEN_KEY, access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
    dispatch({ type: 'LOGIN_SUCCESS', user, token: access_token })
    return true
  }, [])

  const register = useCallback(async (name, username, password, role) => {
    dispatch({ type: 'LOGIN_START' })
    const result = await apiRegister(name, username, password, role)

    if (!result.ok) {
      dispatch({ type: 'LOGIN_FAIL', error: result.error })
      return false
    }

    const { access_token, user } = result.data
    localStorage.setItem(TOKEN_KEY, access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
    dispatch({ type: 'LOGIN_SUCCESS', user, token: access_token })
    return true
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, login, logout, register }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}
