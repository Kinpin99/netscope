export const getSeverityColor = (score) => {
  if (score >= 71) return 'var(--crit)';
  if (score >= 31) return 'var(--warn)';
  return 'var(--info)';
};

export const getSeverityLabel = (score) => {
  if (score >= 71) return 'critical';
  if (score >= 31) return 'warning';
  return 'info';
};

export const getHealthColor = (score) => {
  if (score >= 70) return 'var(--ok)';
  if (score >= 40) return 'var(--warn)';
  return 'var(--crit)';
};

export const getStatusColor = (status) => {
  if (status === 'online') return 'var(--ok)';
  if (status === 'degraded') return 'var(--warn)';
  return 'var(--crit)';
};
