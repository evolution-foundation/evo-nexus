import { useEffect, useMemo, useRef, useState } from 'react'
import MentionAutocomplete from './MentionAutocomplete'
import { api } from '../lib/api'
import { filterMentionAgents, findMentionContext, insertMention, type MentionAgent, type MentionContext } from '../lib/mentionAutocomplete'

interface Props {
  assignee: string | null
  submitting: boolean
  onSubmit: (body: string) => Promise<boolean>
}

export default function CommentComposer({ assignee, submitting, onSubmit }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [body, setBody] = useState('')
  const [context, setContext] = useState<MentionContext | null>(null)
  const [agents, setAgents] = useState<MentionAgent[] | null>(null)
  const [agentError, setAgentError] = useState(false)
  const [index, setIndex] = useState(0)
  const options = useMemo(() => context && agents ? filterMentionAgents(agents, context.query, assignee) : [], [agents, assignee, context])
  const safeIndex = options.length === 0 ? 0 : Math.min(index, options.length - 1)
  const selected = options[safeIndex]

  useEffect(() => {
    if (!context || agents !== null || agentError) return
    api.get('/agents').then((items: MentionAgent[]) => setAgents(items)).catch(() => setAgentError(true))
  }, [agentError, agents, context])

  const updateContext = (value: string, caret: number) => {
    setBody(value)
    setContext(findMentionContext(value, caret))
    setIndex(0)
  }

  const select = (agent: MentionAgent) => {
    if (!context) return
    const next = insertMention(body, context, agent.name)
    setBody(next.value)
    setContext(null)
    requestAnimationFrame(() => {
      textareaRef.current?.focus()
      textareaRef.current?.setSelectionRange(next.caret, next.caret)
    })
  }

  const keyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!context) return
    if (event.key === 'Escape') { event.preventDefault(); setContext(null); return }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (options.length) setIndex((safeIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
      return
    }
    if ((event.key === 'Enter' || event.key === 'Tab') && selected) { event.preventDefault(); select(selected) }
  }

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting || !body.trim()) return
    if (await onSubmit(body.trim())) {
      setBody('')
      setContext(null)
      setIndex(0)
    }
  }

  return <form onSubmit={submit}>
    <div className="relative">
      <textarea ref={textareaRef} role="combobox" aria-autocomplete="list" aria-expanded={!!context} aria-controls="mention-suggestions" aria-activedescendant={selected ? `mention-suggestions-${safeIndex}` : undefined} className="w-full bg-[#0C111D] border border-[#21262d] rounded-lg px-3 py-2 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 resize-none transition-colors" placeholder="Add a comment... Use @agent-slug to mention an agent" rows={3} value={body} onChange={event => updateContext(event.target.value, event.target.selectionStart ?? event.target.value.length)} onClick={event => { setIndex(0); setContext(findMentionContext(event.currentTarget.value, event.currentTarget.selectionStart ?? event.currentTarget.value.length)) }} onKeyDown={keyDown} onBlur={event => requestAnimationFrame(() => { if (document.activeElement !== event.currentTarget) setContext(null) })} />
      <MentionAutocomplete agents={agents} error={agentError} context={context} assignee={assignee} selectedIndex={safeIndex} onSelect={select} onSelectedIndexChange={setIndex} anchorRef={textareaRef} listboxId="mention-suggestions" />
    </div>
    <div className="flex items-center justify-between mt-3">
      <p className="text-[10px] text-[#667085]">Tip: @mention an agent to wake their heartbeat</p>
      <button type="submit" disabled={submitting || !body.trim()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-[#00FFA7] text-black rounded-lg hover:bg-[#00FFA7]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">{submitting ? 'Sending...' : 'Comment'}</button>
    </div>
  </form>
}
