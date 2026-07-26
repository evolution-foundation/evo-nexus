interface HealthBadgeProps {
  status: 'healthy' | 'warning' | 'critical' | 'neutral'
  label: string
}

export default function HealthBadge({ status, label }: HealthBadgeProps) {
  const styles = {
    healthy: 'bg-[#00FFA7]/15 text-[#00FFA7] border-[#00FFA7]/30',
    warning: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    critical: 'bg-red-500/15 text-red-400 border-red-500/30',
    neutral: 'bg-[#667085]/15 text-[#98A2B3] border-[#667085]/30',
  }[status]

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${styles}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${
        status === 'healthy' ? 'bg-[#00FFA7]' : status === 'warning' ? 'bg-yellow-400' : status === 'critical' ? 'bg-red-400' : 'bg-[#98A2B3]'
      }`} />
      {label}
    </span>
  )
}
