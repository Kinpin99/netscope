import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { SystemProvider } from './context/SystemContext'
import { AlertProvider } from './context/AlertContext'
import Shell from './components/layout/Shell'
import NotificationStack from './components/NotificationStack'
import Login from './pages/Login'
import Overview from './pages/Overview'
import Devices from './pages/Devices'
import DeviceDetail from './pages/DeviceDetail'
import Alerts from './pages/Alerts'
import Traffic from './pages/Traffic'

function ProtectedRoutes() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
        <div className="skeleton" style={{ width: 120, height: 16 }} />
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <SystemProvider>
      <AlertProvider>
        <Shell>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/devices/:ip" element={<DeviceDetail />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/traffic" element={<Traffic />} />
            <Route path="/settings" element={<SettingsPlaceholder />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
        <NotificationStack />
      </AlertProvider>
    </SystemProvider>
  )
}

function LoginRoute() {
  const { user, loading } = useAuth()
  if (loading) return null
  if (user) return <Navigate to="/" replace />
  return <Login />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginRoute />} />
          <Route path="/*" element={<ProtectedRoutes />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

function SettingsPlaceholder() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 20px', color: 'var(--text-dim)' }}>
      <span style={{ fontSize: 13 }}>System settings</span>
      <span style={{ fontSize: 11, marginTop: 4 }}>Configuration options will appear here.</span>
    </div>
  )
}
