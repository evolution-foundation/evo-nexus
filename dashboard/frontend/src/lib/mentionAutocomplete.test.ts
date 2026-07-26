import { describe, expect, it } from 'vitest'
import {
  filterMentionAgents,
  findMentionContext,
  insertMention,
  summarizeAgentDescription,
  type MentionAgent,
} from './mentionAutocomplete'

const agents: MentionAgent[] = [
  { name: 'atlas-project', description: 'Projetos e bloqueios', locked: false },
  { name: 'zara-cs', description: 'Suporte ao cliente', locked: false },
  { name: 'vault-security', description: 'Auditoria de segurança', locked: true },
]

describe('findMentionContext', () => {
  it('detecta menção no início e depois de whitespace até o cursor', () => {
    expect(findMentionContext('@at', 3)).toEqual({ start: 0, end: 3, query: 'at' })
    expect(findMentionContext('Olá @za depois', 7)).toEqual({ start: 4, end: 7, query: 'za' })
  })

  it('não detecta arroba no meio de uma palavra', () => {
    expect(findMentionContext('email@atlas', 11)).toBeNull()
  })
})

describe('filterMentionAgents', () => {
  it('filtra slug e descrição sem diferenciar maiúsculas, prioriza assignee e remove locked', () => {
    expect(filterMentionAgents(agents, '', 'zara-cs').map(agent => agent.name))
      .toEqual(['zara-cs', 'atlas-project'])
    expect(filterMentionAgents(agents, 'PRO', null).map(agent => agent.name))
      .toEqual(['atlas-project'])
    expect(filterMentionAgents(agents, 'security', null)).toEqual([])
  })
})

describe('insertMention', () => {
  it('substitui somente o token ativo, preserva o sufixo e retorna caret correto', () => {
    expect(insertMention('Antes @za depois @atlas', { start: 6, end: 9, query: 'za' }, 'zara-cs'))
      .toEqual({ value: 'Antes @zara-cs depois @atlas', caret: 14 })
  })

  it('adiciona espaço depois da menção quando o próximo caractere não é whitespace', () => {
    expect(insertMention('@at, tudo bem', { start: 0, end: 3, query: 'at' }, 'atlas-project'))
      .toEqual({ value: '@atlas-project, tudo bem', caret: 14 })
  })
})

describe('summarizeAgentDescription', () => {
  it('remove prompt internals and returns a compact human-readable summary', () => {
    const description = 'Use this agent when the user needs operational and strategic support — managing agenda, emails, tasks, meetings, prioritization, decision-making, research, documentation, or any form of organized execution.\\n\\nExamples:\\n<commentary>Internal routing instructions</commentary>'

    const summary = summarizeAgentDescription(description)

    expect(summary).toBe('Operational and strategic support — managing agenda, emails, tasks, meetings, prioritization, decision-making…')
    expect(summary).not.toMatch(/Examples|commentary|\\n/)
  })

  it('normalizes tags, escaped whitespace and empty descriptions', () => {
    expect(summarizeAgentDescription('  <b>Research</b>\\n\\tand   documentation.  ')).toBe('Research and documentation.')
    expect(summarizeAgentDescription('')).toBe('')
  })
})
