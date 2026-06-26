import { createContext, useContext } from 'react'
import { usePolling } from '../hooks/usePolling'
import { getSystemStatus } from '../api/system'

const SystemContext = createContext(null)

export function SystemProvider({ children }) {
  const { data: status, loading, error, refresh } = usePolling(getSystemStatus, 10_000)

  return (
    <SystemContext.Provider value={{ status, loading, error, refresh }}>
      {children}
    </SystemContext.Provider>
  )
}

export const useSystem = () => useContext(SystemContext)
