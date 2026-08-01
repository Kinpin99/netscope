import { createContext,useContext,useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { getTrafficRecent,getLiveScores } from '../api/traffic'
const TrafficContext=createContext(null)
export function TrafficProvider({children}){
  const trafficFetch=useCallback(()=>getTrafficRecent(30,80),[])
  const scoreFetch=useCallback(()=>getLiveScores(3),[])
  const traffic=usePolling(trafficFetch,10000)
  const scores=usePolling(scoreFetch,10000)
  return <TrafficContext.Provider value={{traffic:traffic.data||{network:[],devices:{}},liveScores:scores.data||[],loading:traffic.loading||scores.loading,error:traffic.error||scores.error,refresh:()=>{traffic.refresh();scores.refresh()}}}>{children}</TrafficContext.Provider>
}
export const useTraffic=()=>useContext(TrafficContext)
