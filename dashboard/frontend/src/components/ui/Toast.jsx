import { useEffect } from 'react';
import { X, AlertTriangle, AlertCircle } from 'lucide-react';
import { formatTimeAgo } from '../../utils/formatters';

const Toast = ({ alert, onDismiss }) => {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(alert.event_id), 8000);
    return () => clearTimeout(timer);
  }, [alert.event_id, onDismiss]);

  const isCritical = alert.severity_score >= 71;
  const borderColor = isCritical ? 'var(--crit)' : 'var(--warn)';
  const Icon = isCritical ? AlertCircle : AlertTriangle;

  return (
    <div
      className="toast-enter relative flex gap-3 p-3 pr-8 rounded-[7px] bg-[var(--surface)] max-w-[360px]"
      style={{
        border: `1px solid ${borderColor}`,
        borderLeft: `3px solid ${borderColor}`,
      }}
    >
      <Icon size={16} style={{ color: borderColor, flexShrink: 0, marginTop: 2 }} />
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-[11px] font-semibold" style={{ color: borderColor }}>
          {alert.anomaly_type.replace(/_/g, ' ')}
        </span>
        <span className="text-[10px] text-[var(--text)] truncate">
          {alert.device_id} · Score {alert.severity_score}/100
        </span>
        <span className="text-[9px] font-mono text-[var(--muted)]">
          via WebSocket · {formatTimeAgo(alert.detected_at)}
        </span>
      </div>
      <button
        onClick={() => onDismiss(alert.event_id)}
        className="absolute top-2 right-2 text-[var(--muted)] hover:text-[var(--text)] transition-colors"
      >
        <X size={12} />
      </button>
    </div>
  );
};

export default Toast;
