import { createContext,useContext,useCallback,useMemo,useRef,useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { getAlerts } from '../api/alerts'
import { score100,epochDate } from '../utils/friendly'
const AlertContext=createContext(null)
const norm=(a={})=>({
  ...a,
  event_id:a.alert_id||a.id,
  device_id:a.device_name||a.entity_id||'Unknown device',
  device_ip:a.entity_id,
  anomaly_type:a.detector||a.issue_type||'network_issue',
  severity_score:score100(a.anomaly_score??a.last_score??a.max_score)??0,
  detected_at:(epochDate(a.window??a.last_window??a.created_at)||new Date()).toISOString(),
  status:a.status||'open',
})
export function AlertProvider({children}){
  const fetcher=useCallback(()=>getAlerts({last_hours:168}),[])
  const {data,loading,error,refresh}=usePolling(fetcher,10000)
  const alerts=useMemo(()=>Array.isArray(data)?data.map(norm).sort((a,b)=>new Date(b.detected_at)-new Date(a.detected_at)):[],[data])
  const openAlerts=useMemo(()=>alerts.filter(a=>a.status==='open'),[alerts])
  const urgentCount=openAlerts.filter(a=>['critical','high'].includes(String(a.severity).toLowerCase())).length
  return <AlertContext.Provider value={{alerts,openAlerts,urgentCount,loading,error,refresh}}>{children}</AlertContext.Provider>
}
export const useAlerts=()=>useContext(AlertContext)
