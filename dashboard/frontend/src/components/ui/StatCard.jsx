import SparklineArea from '../charts/SparklineArea';

const StatCard = ({ label, value, percentage, percentageLabel, chartData, chartColor, statusLine, anomalyPoints }) => {
  return (
    <div className="bg-[var(--surface)] border border-[var(--border)] rounded-[7px] p-4 flex flex-col gap-2">
      <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--muted)] font-semibold">
        {label}
      </span>
      <div className="text-[26px] font-extrabold tracking-[-0.03em] text-[var(--text)] leading-none">
        {value}
      </div>
      {percentage !== undefined && (
        <span className="text-[11px]" style={{ color: chartColor }}>
          {percentage} — {percentageLabel}
        </span>
      )}
      <SparklineArea data={chartData} color={chartColor} height={48} anomalyPoints={anomalyPoints} />
      {statusLine && (
        <span className="text-[10px] text-[var(--muted)]">{statusLine}</span>
      )}
    </div>
  );
};

export default StatCard;
