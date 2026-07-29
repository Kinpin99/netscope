import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Poll without overlapping requests. Polling pauses while the tab is hidden,
 * which prevents slow traffic queries from stacking up and freezing the UI.
 */
export function usePolling(fetcher, intervalMs) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const inFlightRef = useRef(false)
  const mountedRef = useRef(true)
  const runRef = useRef(null)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timerId = null

    const run = async ({ force = false } = {}) => {
      if (cancelled || inFlightRef.current) return
      if (!force && typeof document !== 'undefined' && document.hidden) return

      inFlightRef.current = true
      try {
        const result = await fetcher()
        if (!cancelled && mountedRef.current) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled && mountedRef.current) {
          // Keep the last good result on transient failures.
          setError(err?.message || 'Unable to refresh data')
        }
      } finally {
        inFlightRef.current = false
        if (!cancelled && mountedRef.current) setLoading(false)
      }
    }

    runRef.current = run

    const tick = async () => {
      await run()
      if (!cancelled) timerId = window.setTimeout(tick, intervalMs)
    }

    tick()

    const onVisibilityChange = () => {
      if (!document.hidden) run({ force: true })
    }
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      if (timerId) window.clearTimeout(timerId)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [fetcher, intervalMs])

  const refresh = useCallback(() => runRef.current?.({ force: true }), [])
  return { data, error, loading, refresh }
}
