import Sidebar from './Sidebar'
import Topbar from './Topbar'
import StatusBanner from '../StatusBanner'

export default function Shell({ children }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <Topbar />
        <StatusBanner />
        <div className="content">
          {children}
        </div>
      </div>
    </div>
  )
}
