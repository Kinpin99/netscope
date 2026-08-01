import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis, ResponsiveContainer, Legend,
} from 'recharts';

const featureLabels = {
  bytes_in_zscore: 'Bytes In',
  bytes_out_zscore: 'Bytes Out',
  tcp_ratio: 'TCP Ratio',
  udp_ratio: 'UDP Ratio',
  icmp_ratio: 'ICMP Ratio',
  distinct_dst_ips_zscore: 'Dst Diversity',
  cpu_util_zscore: 'CPU Usage',
  // hour_cos and hour_sin are combined
  time_pattern: 'Time Pattern',
};

const BehaviourRadar = ({ current, baseline, height = 300 }) => {
  if (!current) return null;

  // combine hour_cos/hour_sin into one "time_pattern" metric
  const timePattern = Math.sqrt(
    Math.pow(current.hour_cos || 0, 2) + Math.pow(current.hour_sin || 0, 2)
  );
  const baselineTime = baseline
    ? Math.sqrt(Math.pow(baseline.hour_cos || 0, 2) + Math.pow(baseline.hour_sin || 0, 2))
    : 0;

  const features = ['bytes_in_zscore', 'bytes_out_zscore', 'tcp_ratio', 'udp_ratio',
    'icmp_ratio', 'distinct_dst_ips_zscore', 'cpu_util_zscore'];

  const data = [
    ...features.map((key) => ({
      feature: featureLabels[key] || key,
      current: Math.abs(current[key] || 0),
      baseline: Math.abs(baseline?.[key] || 0),
    })),
    {
      feature: featureLabels.time_pattern,
      current: timePattern,
      baseline: baselineTime,
    },
  ];

  // figure out if this is anomalous
  const maxScore = Math.max(...data.map((d) => d.current));
  const currentColor = maxScore > 2 ? 'var(--crit)' : 'var(--ok)';

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
          <PolarGrid stroke="var(--border)" />
          <PolarAngleAxis
            dataKey="feature"
            tick={{ fontSize: 9, fill: 'var(--muted)' }}
          />
          <PolarRadiusAxis
            tick={{ fontSize: 8, fill: 'var(--muted)' }}
            axisLine={false}
          />
          <Radar
            name="7-day Baseline"
            dataKey="baseline"
            stroke="var(--muted)"
            fill="var(--muted)"
            fillOpacity={0.1}
            strokeDasharray="4 4"
          />
          <Radar
            name="Current"
            dataKey="current"
            stroke={currentColor}
            fill={currentColor}
            fillOpacity={0.2}
          />
          <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
        </RadarChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-[var(--muted)] text-center mt-2">
        Deviation from 7-day behavioural baseline — Isolation Forest model
      </p>
    </div>
  );
};

export default BehaviourRadar;
