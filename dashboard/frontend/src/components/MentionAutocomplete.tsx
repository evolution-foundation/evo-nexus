import { useEffect, useRef, useState, type RefObject } from 'react'
import {
  DEFAULT_MENTION_MENU_HEIGHT,
  filterMentionAgents,
  mentionMenuGeometry,
  summarizeAgentDescription,
  type MentionAgent,
  type MentionContext,
  type MentionMenuGeometry,
} from '../lib/mentionAutocomplete'

interface Props {
  agents: MentionAgent[] | null
  error: boolean
  context: MentionContext | null
  assignee: string | null
  selectedIndex: number
  onSelect: (agent: MentionAgent) => void
  onSelectedIndexChange: (index: number) => void
  anchorRef: RefObject<HTMLTextAreaElement | null>
  listboxId: string
}


export default function MentionAutocomplete({
  agents,
  error,
  context,
  assignee,
  selectedIndex,
  onSelect,
  onSelectedIndexChange,
  anchorRef,
  listboxId,
}: Props) {
  const listboxRef = useRef<HTMLDivElement>(null)
  const [geometry, setGeometry] = useState<MentionMenuGeometry>({ direction: 'below', maxHeight: DEFAULT_MENTION_MENU_HEIGHT })
  const options = agents && context ? filterMentionAgents(agents, context.query, assignee) : []

  useEffect(() => {
    const anchor = anchorRef.current
    if (!context || !anchor) return
    const update = () => setGeometry(mentionMenuGeometry(anchor.getBoundingClientRect()))
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [anchorRef, context])

  useEffect(() => {
    const option = listboxRef.current?.querySelector<HTMLElement>(`#${listboxId}-${selectedIndex}`)
    if (option && typeof option.scrollIntoView === 'function') option.scrollIntoView({ block: 'nearest' })
  }, [listboxId, selectedIndex])

  if (!context) return null

  const menu = <div
    ref={listboxRef}
    id={listboxId}
    role="listbox"
    aria-label="Sugestões de agentes"
    className="w-full max-w-full overflow-y-auto rounded-lg border border-[#30363d] bg-[#161b22] shadow-xl sm:max-w-2xl"
    style={{ maxHeight: geometry.maxHeight }}
  >
    {agents === null && !error && <p className="px-3 py-2 text-sm text-[#8b949e]">Carregando agentes...</p>}
    {error && <p className="px-3 py-2 text-sm text-red-400">Não foi possível carregar agentes.</p>}
    {agents !== null && !error && options.length === 0 && <p className="px-3 py-2 text-sm text-[#8b949e]">Nenhum agente disponível.</p>}
    {options.map((agent, index) => {
      const summary = summarizeAgentDescription(agent.description)
      const isAssignee = agent.name === assignee
      return <button
        key={agent.name}
        id={`${listboxId}-${index}`}
        type="button"
        role="option"
        aria-selected={index === selectedIndex}
        className={`flex min-h-14 w-full flex-col justify-center px-3 py-2 text-left text-sm ${index === selectedIndex ? 'bg-[#00FFA7]/15 text-[#00FFA7]' : 'text-[#e6edf3] hover:bg-[#21262d]'}`}
        onMouseDown={event => event.preventDefault()}
        onClick={() => onSelect(agent)}
        onMouseEnter={() => onSelectedIndexChange(index)}
      >
        <span className="flex w-full min-w-0 items-center justify-between gap-3">
          <span className="truncate font-mono">@{agent.name}</span>
          {isAssignee && <span className="shrink-0 rounded border border-[#00FFA7]/30 bg-[#00FFA7]/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#00FFA7]">Assigned</span>}
        </span>
        {summary && <span className="mt-0.5 block w-full truncate text-xs leading-5 text-[#8b949e]">{summary}</span>}
      </button>
    })}
  </div>

  if (geometry.direction === 'above') {
    return <div className="absolute inset-x-0 bottom-full mb-2 z-10">{menu}</div>
  }

  return <div className="mt-2">{menu}</div>
}
