import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import MentionAutocomplete from './MentionAutocomplete'
import { mentionMenuGeometry } from '../lib/mentionAutocomplete'

const context = { start: 0, end: 1, query: '' }
const agents = Array.from({ length: 12 }, (_, index) => ({
  name: `agent-${index}`,
  description: index === 0
    ? 'Use this agent when the user needs operational support.\\n\\nExamples:\\n<commentary>Internal prompt</commentary>'
    : 'Agente',
  locked: false,
}))

describe('MentionAutocomplete', () => {
  it('mostra opções selecionáveis e omite locked', () => {
    const onSelect = vi.fn()
    render(<MentionAutocomplete agents={[...agents, { name: 'vault', description: 'locked', locked: true }]} error={false} context={context} assignee="agent-0" selectedIndex={0} onSelect={onSelect} onSelectedIndexChange={vi.fn()} anchorRef={{ current: null }} listboxId="mentions" />)
    expect(screen.getByRole('option', { name: /@agent-0/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Assigned')).toBeInTheDocument()
    expect(screen.getByText('Operational support.')).toBeInTheDocument()
    expect(screen.queryByText(/Examples|Internal prompt/)).not.toBeInTheDocument()
    expect(screen.queryByText('@vault')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /@agent-0/i }))
    expect(onSelect).toHaveBeenCalledWith(agents[0])
  })

  it('faz scroll da opção ativa em lista longa', () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    const { rerender } = render(<MentionAutocomplete agents={agents} error={false} context={context} assignee={null} selectedIndex={0} onSelect={vi.fn()} onSelectedIndexChange={vi.fn()} anchorRef={{ current: null }} listboxId="mentions" />)
    rerender(<MentionAutocomplete agents={agents} error={false} context={context} assignee={null} selectedIndex={10} onSelect={vi.fn()} onSelectedIndexChange={vi.fn()} anchorRef={{ current: null }} listboxId="mentions" />)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
  })

  it('calcula direção e altura em viewport pequena', () => {
    expect(mentionMenuGeometry({ top: 260, bottom: 480 } as DOMRect, 500)).toEqual({ direction: 'above', maxHeight: 248 })
    expect(mentionMenuGeometry({ top: 20, bottom: 100 } as DOMRect, 500)).toEqual({ direction: 'below', maxHeight: 288 })
    expect(mentionMenuGeometry({ top: 10, bottom: 490 } as DOMRect, 500)).toEqual({ direction: 'below', maxHeight: 0 })
  })
})
