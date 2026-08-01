import SparklineArea from '../charts/SparklineArea';

const DetectorCard = ({ name, model, isUnsupervised, score, scoreLabel, chartData, chartColor, statusLine }) => {
  return (
    <div className="bg-[var(--surface)] border border-[var(--border)] rounded-[7px] p-4 flex flex-col gap-2">
      <div>
        <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--muted)] font-semibold">
          {name}
        </span>
        {isUnsupervised && (
          <span className="block text-[9px] text-[var(--muted)] italic mt-0.5">(Unsupervised)</span>
        )}
      </div>
      <div className="text-[26px] font-extrabold tracking-[-0.03em] text-[var(--text)] leading-none">
        {score}
      </div>
      <span className="text-[11px]" style={{ color: chartColor }}>
        {score}% {scoreLabel}
      </span>
      <SparklineArea data={chartData} color={chartColor} height={36} />
      {/* thin progress bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-[3px] rounded-full bg-[var(--border)]">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{ width: `${score}%`, background: chartColor }}
          />
        </div>
        <span className="text-[10px] font-mono text-[var(--muted)]">{score}%</span>
      </div>
      {statusLine && (
        <span className="text-[10px] text-[var(--muted)]">{statusLine}</span>
      )}
    </div>
  );
};

export default DetectorCard;
