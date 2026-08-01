import { ChevronDown,ChevronRight } from 'lucide-react'
import { friendlyCheck,friendlySeverity,severityColor } from '../../utils/friendly'
import { formatTimeAgo } from '../../utils/formatters'
export default function FriendlyAlertItem({alert,expanded,onToggle,compact=false}){
 const color=severityColor(alert.severity)
 return <div className="border border-[var(--border)] rounded-[7px] overflow-hidden">
  <button onClick={onToggle} className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[.015]">
   <span className="w-[7px] h-[7px] rounded-full shrink-0" style={{background:color,boxShadow:['critical','high'].includes(String(alert.severity).toLowerCase())?`0 0 6px ${color}`:'none'}}/>
   <span className="text-[10px] font-bold uppercase min-w-[62px]" style={{color}}>{friendlySeverity(alert.severity)}</span>
   <span className="text-[11px] text-[var(--text)] flex-1 truncate">{friendlyCheck(alert.anomaly_type)} · {alert.device_id}</span>
   <span className="text-[10px] font-mono text-[var(--muted)]">Score {alert.severity_score}</span>
   <span className="text-[10px] text-[var(--muted)] w-[64px] text-right">{formatTimeAgo(alert.detected_at)}</span>
   {onToggle&&(expanded?<ChevronDown size={14}/>:<ChevronRight size={14}/>)}
  </button>
  {expanded&&!compact&&<div className="border-t border-[var(--border)] px-4 py-3 grid grid-cols-2 gap-3 text-[11px]">
   <div><span className="text-[var(--muted)]">Device</span><div className="font-mono mt-1">{alert.device_ip||alert.device_id}</div></div>
   <div><span className="text-[var(--muted)]">First seen</span><div className="mt-1">{new Date(alert.detected_at).toLocaleString()}</div></div>
   <div><span className="text-[var(--muted)]">Current state</span><div className="mt-1 capitalize">{alert.status==='open'?'Still happening':'Ended'}</div></div>
   <div><span className="text-[var(--muted)]">Highest score</span><div className="mt-1 font-mono">{alert.severity_score}/100</div></div>
  </div>}
 </div>
}
