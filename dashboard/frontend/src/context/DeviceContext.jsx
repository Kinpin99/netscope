import { createContext,useContext,useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { getDevices } from '../api/topology'
const DeviceContext=createContext(null)
export function DeviceProvider({children}){
  const fetcher=useCallback(()=>getDevices(),[])
  const {data,loading,error,refresh}=usePolling(fetcher,15000)
  return <DeviceContext.Provider value={{devices:data||[],loading,error,refresh}}>{children}</DeviceContext.Provider>
}
export const useDevices=()=>useContext(DeviceContext)
