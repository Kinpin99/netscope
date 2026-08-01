export const checkLabels = {
  bandwidth: 'Heavy traffic', bandwidth_spike: 'Heavy traffic',
  portscan: 'Port scan', port_scan: 'Port scan',
  device_behavior: 'Unusual device activity', device_behaviour: 'Unusual device activity',
  protocol: 'Unexpected network use', suspicious_protocol: 'Unexpected network use',
}
export const friendlyCheck = (value='') => checkLabels[value] || String(value).replaceAll('_',' ').replace(/\b\w/g, c => c.toUpperCase())
export const friendlySeverity = (value='info') => ({critical:'Urgent',high:'High',medium:'Attention',warning:'Attention',low:'Notice',info:'Notice'}[String(value).toLowerCase()] || 'Notice')
export const severityColor = (value='info') => {
  const v=String(value).toLowerCase(); if(['critical','high'].includes(v)) return 'var(--crit)'; if(['medium','warning'].includes(v)) return 'var(--warn)'; return 'var(--info)'
}
export const friendlyStatus = (value='unknown') => ({healthy:'Good',online:'Good',degraded:'Needs attention',critical:'Urgent',offline:'Offline',unknown:'No recent data',open:'Open',closed:'Ended',pending:'Waiting for approval',config_ready:'Ready to finish',provisioned:'Ready',rejected:'Rejected'}[String(value).toLowerCase()] || String(value).replaceAll('_',' '))
export const score100 = (value) => { const n=Number(value); if(!Number.isFinite(n)) return null; return Math.round(n<=1?n*100:n) }
export const epochDate = (value) => { if(!value) return null; const n=Number(value); return new Date(Number.isFinite(n) ? (n<1e12?n*1000:n) : value) }
export const deviceTypeLabel = (value='device') => ({access_point:'Access point',wireless_ap:'Access point',ap:'Access point',router:'Router',switch:'Switch',server:'Server',host:'Computer',client:'Computer'}[String(value).toLowerCase()] || String(value).replaceAll('_',' '))
