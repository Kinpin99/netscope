import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { formatTime } from '../utils/format'

export default function PacketChart({ data, height = 120 }) {
  if (!data?.length) return null
  const chartData = data.map(d => ({
    time: formatTime(d.window),
    packets_in: d.packets_in,
    packets_out: d.packets_out,
  }))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="time" tick={{ fontSize: 9, fill: 'var(--text-dim)', fontFamily: "'IBM Plex Mono', monospace" }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} interval="preserveStartEnd" minTickGap={50} />
        <YAxis tick={{ fontSize: 9, fill: 'var(--text-dim)' }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', fontSize: 10 }} />
        <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
        <Line type="monotone" dataKey="packets_in" name="Packets In" stroke="var(--accent)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="packets_out" name="Packets Out" stroke="var(--sev-low)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}
