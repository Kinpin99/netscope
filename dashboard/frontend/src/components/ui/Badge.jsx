import { getStatusColor, getSeverityColor } from '../../utils/severity';

const statusStyles = {
  online: 'bg-[rgba(45,212,160,0.1)] text-[var(--ok)]',
  degraded: 'bg-[rgba(245,166,35,0.1)] text-[var(--warn)]',
  offline: 'bg-[rgba(232,68,90,0.1)] text-[var(--crit)]',
};

const severityStyles = {
  info: 'bg-[rgba(107,122,153,0.1)] text-[var(--info)]',
  warning: 'bg-[rgba(245,166,35,0.1)] text-[var(--warn)]',
  critical: 'bg-[rgba(232,68,90,0.1)] text-[var(--crit)]',
};

const Badge = ({ status, severity }) => {
  const label = status || severity;
  const styles = status ? statusStyles[status] : severityStyles[severity];

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${styles || ''}`}>
      {label}
    </span>
  );
};

export default Badge;
