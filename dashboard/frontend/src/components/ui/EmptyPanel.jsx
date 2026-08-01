import { CircleAlert } from 'lucide-react'
export default function EmptyPanel({message='Nothing to show yet.'}){return <div className="min-h-[140px] flex flex-col items-center justify-center text-[var(--muted)] gap-3"><CircleAlert size={22}/><span className="text-[11px]">{message}</span></div>}
