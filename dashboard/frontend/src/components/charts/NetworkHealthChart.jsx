import { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  ReferenceLine, ReferenceDot, ResponsiveContainer, Tooltip,
} from 'recharts';
import { getHealthColor } from '../../utils/severity';

const CustomDot = (props) => {
  const { cx, cy, payload } = props;
  if (!payload.anomaly) return null;

  const color = payload.anomaly.severity === 'critical' ? 'var(--crit)' : 'var(--warn)';
  return <circle cx={cx} cy={cy} r={4} fill={color} stroke="none" style={{ cursor: 'pointer' }} />;
};

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.[0]) return null;
  const data = payload[0].payload;
  return (
    <div className="bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-2 text-[10px]">
      <div className="font-mono text-[var(--text)]">Score: {data.score}</div>
      <div className="text-[var(--muted)]">{new Date(data.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</div>
      {data.anomaly && (
        <div className="mt-1" style={{ color: data.anomaly.severity === 'critical' ? 'var(--crit)' : 'var(--warn)' }}>
          {data.anomaly.type.replace(/_/g, ' ')} — score {data.anomaly.score}
        </div>
      )}
    </div>
  );
};

const NetworkHealthChart = ({ data, height = 200 }) => {
  const chartData = useMemo(() => {
    if (!data) return [];
    return data.map((d) => ({
      ...d,
      time: new Date(d.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
    }));
  }, [data]);

  // anomaly reference lines
  const anomalyLines = chartData.filter((d) => d.anomaly);

  // dynamic gradient — we'll just use green since the area shifts
  const gradientId = 'health-area-gradient';

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--ok)" stopOpacity={0.2} />
            <stop offset="100%" stopColor="var(--ok)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: 'var(--muted)', fontFamily: "'Courier New', monospace" }}
          axisLine={{ stroke: 'var(--border)' }}
          tickLine={false}
          interval="preserveStartEnd"
          minTickGap={60}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tick={{ fontSize: 10, fill: 'var(--muted)' }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<CustomTooltip />} />
        <Area
          type="monotone"
          dataKey="score"
          stroke="var(--ok)"
          strokeWidth={2}
          fill={`url(#${gradientId})`}
          dot={<CustomDot />}
          isAnimationActive={false}
        />
        {anomalyLines.map((d, i) => (
          <ReferenceLine
            key={i}
            x={d.time}
            stroke={d.anomaly.severity === 'critical' ? 'var(--crit)' : 'var(--warn)'}
            strokeDasharray="4 4"
            strokeOpacity={0.5}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
};

export default NetworkHealthChart;
