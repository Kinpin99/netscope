import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar';import Topbar from './Topbar';import PageTabs from './PageTabs'
export default function Shell(){return <div className="flex h-screen"><Sidebar/><div className="flex flex-col flex-1 min-w-0"><Topbar/><PageTabs/><main className="flex-1 overflow-y-auto p-5"><Outlet/></main></div></div>}
