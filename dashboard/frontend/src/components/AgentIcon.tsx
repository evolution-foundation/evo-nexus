import { getAgentMeta } from '../lib/agent-meta'

interface AgentIconProps {
  agent: string
  size?: number
}

export function AgentIcon({ agent, size = 24 }: AgentIconProps) {
  const meta = getAgentMeta(agent)
  const initials = agent
    .split('-')
    .slice(0, 2)
    .map(p => p[0]?.toUpperCase() ?? '')
    .join('')

  if (meta.avatar) {
    return (
      <img
        src={meta.avatar}
        alt={agent}
        width={size}
        height={size}
        className="rounded object-cover flex-shrink-0"
        style={{ width: size, height: size }}
        onError={e => {
          ;(e.currentTarget as HTMLImageElement).style.display = 'none'
        }}
      />
    )
  }

  return (
    <div
      className="rounded flex items-center justify-center flex-shrink-0 text-[9px] font-bold"
      style={{
        width: size,
        height: size,
        backgroundColor: `${meta.color}22`,
        color: meta.color,
        border: `1px solid ${meta.color}40`,
      }}
    >
      {initials}
    </div>
  )
}
