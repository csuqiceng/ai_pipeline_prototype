import { AlertTriangle, CheckCircle2 } from 'lucide-react';

export type StatusTone = 'success' | 'warning' | 'error' | 'neutral';

interface StatusRowProps {
  label: string;
  value: string;
  tone?: StatusTone;
}

export default function StatusRow({ label, value, tone = 'neutral' }: StatusRowProps) {
  const toneClass =
    tone === 'success'
      ? 'text-primary'
      : tone === 'warning'
        ? 'text-warning'
        : tone === 'error'
          ? 'text-danger'
          : 'text-on-surface';

  return (
    <div className="flex items-center justify-between gap-3 text-[10px]">
      <span className="min-w-0 text-text-secondary">{label}</span>
      <span className={`flex shrink-0 items-center gap-1 font-bold ${toneClass}`}>
        {tone === 'success' && <CheckCircle2 size={10} />}
        {(tone === 'warning' || tone === 'error') && <AlertTriangle size={10} />}
        {value}
      </span>
    </div>
  );
}
