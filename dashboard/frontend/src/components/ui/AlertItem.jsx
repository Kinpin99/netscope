import { ChevronDown, ChevronRight } from 'lucide-react';
import { getSeverityColor } from '../../utils/severity';
import { formatTime, formatTimeAgo } from '../../utils/formatters';

const AlertItem = ({ alert, expanded, onToggle, onAcknowledge, onResolve }) => {
  const color = getSeverityColor(alert.severity_score);
  const isResolved = alert.status === 'resolved';
  const isAcknowledged = alert.status === 'acknowledged';

  return (
    <div
      className={`border border-[var(--border)] rounded-[7px] transition-opacity ${isResolved ? 'opacity-50' : isAcknowledged ? 'opacity-75' : ''}`}
    >
      {/* collapsed row */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[rgba(255,255,255,0.015)] transition-colors"
      >
        <span
          className="w-[7px] h-[7px] rounded-full flex-shrink-0"
          style={{
            background: color,
            boxShadow: alert.severity === 'critical' ? `0 0 5px ${color}` : 'none',
          }}
        />
        <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded" style={{ color }}>
          {alert.severity}
        </span>
        <span className="text-[11px] text-[var(--text)] flex-1 truncate">
          {alert.anomaly_type.replace(/_/g, ' ')} · {alert.device_id}
        </span>
        <span className="text-[10px] font-mono text-[var(--muted)] flex-shrink-0">
          Score {alert.severity_score}
        </span>
        <span className="text-[10px] font-mono text-[var(--muted)] flex-shrink-0 w-[60px] text-right">
          {formatTime(alert.detected_at)}
        </span>
        {expanded ? <ChevronDown size={14} className="text-[var(--muted)]" /> : <ChevronRight size={14} className="text-[var(--muted)]" />}
      </button>

      {/* expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-[var(--border)]">
          <div className="flex flex-col gap-3">
            <div className="text-[11px] text-[var(--muted)] flex flex-wrap gap-x-4 gap-y-1">
              <span>Device: <span className="text-[var(--text)] font-mono">{alert.device_id}</span></span>
              <span>Detector: <span className="text-[var(--text)]">{alert.anomaly_type.replace(/_/g, ' ')}</span></span>
              <span>Score: <span className="text-[var(--text)] font-mono">{alert.severity_score} / 100</span></span>
            </div>

            {/* detected features */}
            {alert.detected_features && Object.keys(alert.detected_features).length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--muted)] font-semibold mb-2">
                  Detected Features
                </div>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1">
                  {Object.entries(alert.detected_features).map(([key, val]) => (
                    <div key={key} className="flex justify-between text-[10px] font-mono py-0.5">
                      <span className="text-[var(--muted)]">{key}</span>
                      <span className="text-[var(--text)]">
                        {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* model explanation */}
            {alert.model_explanation && (
              <div>
                <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--muted)] font-semibold mb-1">
                  Model Explanation
                </div>
                <p className="text-[11px] text-[var(--text)] leading-relaxed">
                  "{alert.model_explanation}"
                </p>
              </div>
            )}

            {/* action buttons */}
            {alert.status === 'open' && (
              <div className="flex gap-2 pt-1">
                <button
                  onClick={(e) => { e.stopPropagation(); onAcknowledge(alert.event_id); }}
                  className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide rounded bg-[var(--surface2)] text-[var(--text)] border border-[var(--border)] hover:border-[var(--border-hi)] transition-colors"
                >
                  Acknowledge
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onResolve(alert.event_id); }}
                  className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide rounded bg-[rgba(45,212,160,0.1)] text-[var(--ok)] border border-[rgba(45,212,160,0.2)] hover:border-[var(--ok)] transition-colors"
                >
                  Resolve
                </button>
              </div>
            )}
            {alert.status === 'acknowledged' && (
              <div className="flex gap-2 pt-1">
                <button
                  onClick={(e) => { e.stopPropagation(); onResolve(alert.event_id); }}
                  className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide rounded bg-[rgba(45,212,160,0.1)] text-[var(--ok)] border border-[rgba(45,212,160,0.2)] hover:border-[var(--ok)] transition-colors"
                >
                  Resolve
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default AlertItem;
