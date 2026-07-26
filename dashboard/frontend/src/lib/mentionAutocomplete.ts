export interface MentionAgent {
  name: string
  description: string
  locked: boolean
}

export interface MentionContext {
  start: number
  end: number
  query: string
}

export interface MentionMenuGeometry {
  direction: 'above' | 'below'
  maxHeight: number
}

const VIEWPORT_GUTTER = 12
const PREFERRED_MENU_HEIGHT = 288
const DESCRIPTION_MAX_LENGTH = 120

export function mentionMenuGeometry(rect: DOMRect, viewportHeight = window.innerHeight): MentionMenuGeometry {
  const above = Math.max(0, rect.top - VIEWPORT_GUTTER)
  const below = Math.max(0, viewportHeight - rect.bottom - VIEWPORT_GUTTER)
  const direction = below >= PREFERRED_MENU_HEIGHT || below >= above ? 'below' : 'above'
  const available = direction === 'below' ? below : above
  return { direction, maxHeight: Math.max(0, Math.min(PREFERRED_MENU_HEIGHT, available)) }
}

export const DEFAULT_MENTION_MENU_HEIGHT = PREFERRED_MENU_HEIGHT

export function summarizeAgentDescription(description: string): string {
  const firstSection = description
    .replace(/\r\n/g, '\n')
    .split(/(?:\\n\s*){2,}|\n\s*\n|\bExamples?:/i, 1)[0]
    .replace(/\\[nrt]/g, ' ')
    .replace(/[\n\r\t]/g, ' ')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\\"/g, '"')
    .replace(/\s+/g, ' ')
    .trim()

  const withoutBoilerplate = firstSection
    .replace(/^Use this agent when the user needs\s+/i, '')
    .replace(/^Use this agent when\s+/i, '')
    .trim()

  if (!withoutBoilerplate) return ''

  const summary = withoutBoilerplate[0].toUpperCase() + withoutBoilerplate.slice(1)
  if (summary.length <= DESCRIPTION_MAX_LENGTH) return summary

  const candidate = summary.slice(0, DESCRIPTION_MAX_LENGTH - 1)
  const wordBoundary = candidate.lastIndexOf(' ')
  const truncated = wordBoundary > DESCRIPTION_MAX_LENGTH / 2
    ? candidate.slice(0, wordBoundary)
    : candidate
  return `${truncated.replace(/[\s,.;:—-]+$/, '')}…`
}

export function findMentionContext(value: string, caret: number): MentionContext | null {
  const before = value.slice(0, caret)
  const match = before.match(/(^|\s)@([\w-]*)$/)
  if (!match) return null

  const query = match[2]
  return {
    start: caret - query.length - 1,
    end: caret,
    query,
  }
}

export function filterMentionAgents(
  agents: MentionAgent[],
  query: string,
  assignee: string | null,
): MentionAgent[] {
  const normalizedQuery = query.toLowerCase()
  return agents
    .filter(agent => !agent.locked)
    .filter(agent => !normalizedQuery || agent.name.toLowerCase().includes(normalizedQuery)
      || agent.description.toLowerCase().includes(normalizedQuery))
    .sort((a, b) => {
      const aIsAssignee = a.name === assignee
      const bIsAssignee = b.name === assignee
      if (aIsAssignee !== bIsAssignee) return aIsAssignee ? -1 : 1
      return a.name.localeCompare(b.name)
    })
}

export function insertMention(
  value: string,
  context: MentionContext,
  agentName: string,
): { value: string; caret: number } {
  const before = value.slice(0, context.start)
  const after = value.slice(context.end)
  const mention = `@${agentName}`
  const suffix = after && !/^[\s.,!?;:)]/.test(after) ? ' ' : ''
  const nextValue = `${before}${mention}${suffix}${after}`

  return { value: nextValue, caret: (before + mention + suffix).length }
}
