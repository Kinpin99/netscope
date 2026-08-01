const SectionLabel = ({ label }) => {
  return (
    <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.1em] text-[var(--muted)] font-semibold">
      {label}
      <div className="flex-1 h-px bg-[var(--border)]" />
    </div>
  );
};

export default SectionLabel;
