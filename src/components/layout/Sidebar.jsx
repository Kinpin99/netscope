import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Monitor, AlertTriangle, Activity, Settings, LogOut } from 'lucide-react'
import { useAlerts } from '../../context/AlertContext'
import { useAuth } from '../../context/AuthContext'

const navSections = [
  {
    label: 'Monitor',
    items: [
      { to: '/', icon: LayoutDashboard, label: 'Overview' },
      { to: '/devices', icon: Monitor, label: 'Devices' },
      { to: '/alerts', icon: AlertTriangle, label: 'Alerts', badge: true },
      { to: '/traffic', icon: Activity, label: 'Traffic' },
    ],
  },
  {
    label: 'System',
    items: [
      { to: '/settings', icon: Settings, label: 'Settings' },
    ],
  },
]

export default function Sidebar() {
  const { openAlerts } = useAlerts()
  const { user, logout } = useAuth()
  const alertCount = openAlerts.length

  const initials = user?.name
    ? user.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : 'U'

  return (
    <aside style={{
      width: 'var(--sidebar-w)', height: '100%', background: 'var(--panel)',
      borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', flexShrink: 0,
    }}>
      {/* logo */}
      <div style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{
          width: 30, height: 30, borderRadius: 7, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <span style={{ color: '#0B0E14', fontWeight: 800, fontSize: 15, lineHeight: 1 }}>N</span>
        </div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.2 }}>NetScope</div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>KNUST NOC</div>
        </div>
      </div>

      {/* nav */}
      <nav style={{ flex: 1, padding: '4px 10px', display: 'flex', flexDirection: 'column', gap: 2, overflowY: 'auto' }}>
        {navSections.map((section) => (
          <div key={section.label} style={{ marginBottom: 8 }}>
            <div style={{ padding: '8px 12px', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-dim)', fontWeight: 600 }}>
              {section.label}
            </div>
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                style={({ isActive }) => ({
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 12px', borderRadius: 'var(--radius)',
                  fontSize: 12, textDecoration: 'none', transition: 'all 0.15s',
                  color: isActive ? 'var(--text)' : 'var(--text-dim)',
                  background: isActive ? 'rgba(74,222,128,0.06)' : 'transparent',
                  borderLeft: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                  marginLeft: isActive ? -1 : 0,
                })}
              >
                <item.icon size={15} />
                <span style={{ flex: 1 }}>{item.label}</span>
                {item.badge && alertCount > 0 && (
                  <span style={{
                    minWidth: 18, height: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    borderRadius: 99, background: 'var(--sev-critical)', color: '#fff',
                    fontSize: 9, fontWeight: 700, padding: '0 5px',
                  }}>
                    {alertCount}
                  </span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      {/* user + logout */}
      <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 28, height: 28, borderRadius: '50%', background: 'var(--panel-alt)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 700, color: 'var(--text-dim)',
        }}>{initials}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user?.name || 'User'}</div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>{user?.role || 'Operator'}</div>
        </div>
        <button className="sidebar-logout" onClick={logout} title="Sign out">
          <LogOut size={14} />
        </button>
      </div>
    </aside>
  )
}
