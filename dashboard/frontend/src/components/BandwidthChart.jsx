import { memo, useId, useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { formatBytes, formatTime } from '../utils/format'

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '8px 12px', fontSize: 10 }}>
      <div className="mono dim" style={{ marginBottom: 4 }}>{label}</div>
      {payload.map(p => (
        <div key={p.dataKey} style={{ color: p.color }}>
          {p.name}: <span className="mono">{formatBytes(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

function BandwidthChart({ data, height = 180 }) {
  const id = useId().replace(/:/g, '')
  const inGradient = `bw-in-${id}`
  const outGradient = `bw-out-${id}`

  const chartData = useMemo(() => (Array.isArray(data) ? data : [])
    .filter(d => Number.isFinite(Number(d?.window)))
    .map(d => ({
      time: formatTime(Number(d.window)),
      bytes_in: Number.isFinite(Number(d.bytes_in)) ? Number(d.bytes_in) : 0,
      bytes_out: Number.isFinite(Number(d.bytes_out)) ? Number(d.bytes_out) : 0,
    })), [data])

  if (!chartData.length) return null

  return (
    <ResponsiveContainer width="100%" height={height} minWidth={0}>
      <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
        <defs>
          <linearGradient id={inGradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
          <linearGradient id={outGradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--sev-low)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--sev-low)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="time" tick={{ fontSize: 9, fill: 'var(--text-dim)', fontFamily: "'IBM Plex Mono', monospace" }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} interval="preserveStartEnd" minTickGap={50} />
        <YAxis tick={{ fontSize: 9, fill: 'var(--text-dim)' }} axisLine={false} tickLine={false} tickFormatter={formatBytes} width={58} />
        <Tooltip content={<CustomTooltip />} isAnimationActive={false} />
        <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
        <Area type="monotone" dataKey="bytes_in" name="In" stroke="var(--accent)" fill={`url(#${inGradient})`} strokeWidth={1.5} isAnimationActive={false} />
        <Area type="monotone" dataKey="bytes_out" name="Out" stroke="var(--sev-low)" fill={`url(#${outGradient})`} strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export default memo(BandwidthChart)
