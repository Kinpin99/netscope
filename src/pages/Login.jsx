import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../context/AuthContext'
import { Shield, Eye, EyeOff, Loader, UserPlus, LogIn } from 'lucide-react'

const ROLES = ['Engineer', 'NOC Admin', 'Analyst']

export default function Login() {
  const { login, register, loading, error } = useAuth()
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [confirmPass, setConfirmPass] = useState('')
  const [role, setRole] = useState(ROLES[0])
  const [showPass, setShowPass] = useState(false)
  const [localError, setLocalError] = useState(null)
  const firstRef = useRef(null)

  useEffect(() => {
    firstRef.current?.focus()
  }, [mode])

  function switchMode(next) {
    setMode(next)
    setUsername('')
    setPassword('')
    setName('')
    setConfirmPass('')
    setRole(ROLES[0])
    setShowPass(false)
    setLocalError(null)
  }

  async function handleLogin(e) {
    e.preventDefault()
    if (!username.trim() || !password.trim() || loading) return
    setLocalError(null)
    await login(username.trim(), password)
  }

  async function handleRegister(e) {
    e.preventDefault()
    if (!name.trim() || !username.trim() || !password.trim() || loading) return
    setLocalError(null)

    if (password.length < 6) {
      setLocalError('Password must be at least 6 characters.')
      return
    }
    if (password !== confirmPass) {
      setLocalError('Passwords do not match.')
      return
    }

    await register(name.trim(), username.trim(), password, role)
  }

  const displayError = localError || error
  const isLogin = mode === 'login'

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-logo">
            <span className="login-logo-letter">N</span>
          </div>
          <div>
            <div className="login-title">NetScope</div>
            <div className="login-subtitle">KNUST Network Operations Center</div>
          </div>
        </div>

        <div className="login-divider" />

        <div className="login-heading">
          {isLogin ? <LogIn size={14} style={{ color: 'var(--text-dim)' }} /> : <UserPlus size={14} style={{ color: 'var(--text-dim)' }} />}
          <span>{isLogin ? 'Sign in to continue' : 'Create an account'}</span>
        </div>

        {displayError && (
          <div className="login-banner login-banner--error">
            <span>{displayError}</span>
          </div>
        )}

        {isLogin ? (
          <form onSubmit={handleLogin} className="login-form">
            <div className="login-field">
              <label className="login-label" htmlFor="username">Username</label>
              <input
                ref={firstRef}
                id="username"
                type="text"
                className="login-input"
                placeholder="Enter username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                disabled={loading}
                autoComplete="username"
              />
            </div>

            <div className="login-field">
              <label className="login-label" htmlFor="password">Password</label>
              <div className="login-input-wrap">
                <input
                  id="password"
                  type={showPass ? 'text' : 'password'}
                  className="login-input login-input--pw"
                  placeholder="Enter password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="current-password"
                />
                <button type="button" className="login-eye" onClick={() => setShowPass(s => !s)} tabIndex={-1}>
                  {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <button type="submit" className="login-btn" disabled={!username.trim() || !password.trim() || loading}>
              {loading ? <Loader size={14} className="login-spinner" /> : 'Sign In'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleRegister} className="login-form">
            <div className="login-field">
              <label className="login-label" htmlFor="reg-name">Full Name</label>
              <input
                ref={firstRef}
                id="reg-name"
                type="text"
                className="login-input"
                placeholder="e.g. Kofi Mensah"
                value={name}
                onChange={e => setName(e.target.value)}
                disabled={loading}
                autoComplete="name"
              />
            </div>

            <div className="login-field">
              <label className="login-label" htmlFor="reg-username">Username</label>
              <input
                id="reg-username"
                type="text"
                className="login-input"
                placeholder="Choose a username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                disabled={loading}
                autoComplete="username"
              />
            </div>

            <div className="login-field">
              <label className="login-label" htmlFor="reg-role">Role</label>
              <select
                id="reg-role"
                className="login-input login-select"
                value={role}
                onChange={e => setRole(e.target.value)}
                disabled={loading}
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>

            <div className="login-field">
              <label className="login-label" htmlFor="reg-password">Password</label>
              <div className="login-input-wrap">
                <input
                  id="reg-password"
                  type={showPass ? 'text' : 'password'}
                  className="login-input login-input--pw"
                  placeholder="Min. 6 characters"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="new-password"
                />
                <button type="button" className="login-eye" onClick={() => setShowPass(s => !s)} tabIndex={-1}>
                  {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <div className="login-field">
              <label className="login-label" htmlFor="reg-confirm">Confirm Password</label>
              <input
                id="reg-confirm"
                type="password"
                className="login-input"
                placeholder="Re-enter password"
                value={confirmPass}
                onChange={e => setConfirmPass(e.target.value)}
                disabled={loading}
                autoComplete="new-password"
              />
            </div>

            <button
              type="submit"
              className="login-btn"
              disabled={!name.trim() || !username.trim() || !password.trim() || !confirmPass.trim() || loading}
            >
              {loading ? <Loader size={14} className="login-spinner" /> : 'Create Account'}
            </button>
          </form>
        )}

        <div className="login-switch">
          {isLogin ? (
            <>
              <span>Don't have an account?</span>
              <button className="login-switch-btn" onClick={() => switchMode('register')}>Sign Up</button>
            </>
          ) : (
            <>
              <span>Already have an account?</span>
              <button className="login-switch-btn" onClick={() => switchMode('login')}>Sign In</button>
            </>
          )}
        </div>

        <div className="login-footer">
          <Shield size={10} />
          <span>Restricted access — authorized NOC personnel only</span>
        </div>
      </div>
    </div>
  )
}
