import { getHealthColor } from '../../utils/severity';

const HealthBar = ({ score, width = 52, height = 3 }) => {
  const color = getHealthColor(score);

  return (
    <div className="flex items-center gap-2">
      <div
        className="rounded-full bg-[var(--border)]"
        style={{ width: `${width}px`, height: `${height}px` }}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${score}%`, background: color }}
        />
      </div>
      <span className="text-[10px] font-mono" style={{ color }}>
        {score}%
      </span>
    </div>
  );
};

export default HealthBar;
