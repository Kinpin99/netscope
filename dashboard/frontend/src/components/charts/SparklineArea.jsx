import { AreaChart, Area, ReferenceDot, ResponsiveContainer } from 'recharts';

const SparklineArea = ({ data, color, height = 48, anomalyPoints = [] }) => {
  if (!data || data.length === 0) return null;

  // need a unique gradient id per instance
  const gradientId = `spark-${color?.replace(/[^a-z0-9]/gi, '') || 'default'}-${Math.random().toString(36).slice(2, 6)}`;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.8}
          fill={`url(#${gradientId})`}
          isAnimationActive={false}
        />
        {anomalyPoints.map((idx) => (
          <ReferenceDot
            key={idx}
            x={data[idx]?.name}
            y={data[idx]?.value}
            r={3}
            fill="var(--crit)"
            stroke="none"
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
};

export default SparklineArea;
