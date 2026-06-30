import { createContext, useContext, useReducer, useCallback, useRef } from 'react'
import { getOpenAlerts } from '../api/alerts'
import { usePolling } from '../hooks/usePolling'

const AlertContext = createContext(null)

function reducer(state, action) {
  switch (action.type) {
    case 'SET_OPEN':
      return { ...state, openAlerts: action.payload }
    case 'ADD_NOTIFICATION':
      return { ...state, notifications: [action.payload, ...state.notifications].slice(0, 3) }
    case 'DISMISS_NOTIFICATION':
      return { ...state, notifications: state.notifications.filter(n => n.id !== action.id) }
    default:
      return state
  }
}

export function AlertProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, {
    openAlerts: [],
    notifications: [],
  })
  const prevIdsRef = useRef(new Set())

  const fetcher = useCallback(async () => {
    const alerts = await getOpenAlerts()
    const currentIds = new Set(alerts.map(a => a.alert_id))

    // fire notifications for newly-appeared alerts
    alerts.forEach(alert => {
      if (!prevIdsRef.current.has(alert.alert_id)) {
        dispatch({ type: 'ADD_NOTIFICATION', payload: { ...alert, id: alert.alert_id } })
      }
    })
    prevIdsRef.current = currentIds
    dispatch({ type: 'SET_OPEN', payload: alerts })
    return alerts
  }, [])

  usePolling(fetcher, 10_000)

  const dismissNotification = useCallback((id) => {
    dispatch({ type: 'DISMISS_NOTIFICATION', id })
  }, [])

  return (
    <AlertContext.Provider value={{ ...state, dismissNotification }}>
      {children}
    </AlertContext.Provider>
  )
}

export const useAlerts = () => useContext(AlertContext)
