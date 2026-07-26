import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CommentComposer from './CommentComposer'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({ api: { get: vi.fn() } }))

const getAgents = vi.mocked(api.get)
const agents = [
  { name: 'atlas-project', description: 'Projetos', locked: false },
  { name: 'zara-cs', description: 'Suporte', locked: false },
  { name: 'vault-security', description: 'Segurança', locked: true },
]

beforeEach(() => vi.resetAllMocks())

describe('CommentComposer', () => {
  it('busca agentes lazy, navega Up/Down, insere por Enter/Tab e preserva suffix/foco/caret', async () => {
    getAgents.mockResolvedValue(agents)
    render(<CommentComposer assignee="zara-cs" submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    const textarea = screen.getByRole('combobox') as HTMLTextAreaElement

    expect(getAgents).not.toHaveBeenCalled()
    fireEvent.change(textarea, { target: { value: 'Antes @ depois', selectionStart: 7 } })
    await waitFor(() => expect(getAgents).toHaveBeenCalledWith('/agents'))
    await screen.findAllByRole('option')
    expect(screen.queryByText('@vault-security')).not.toBeInTheDocument()
    fireEvent.keyDown(textarea, { key: 'ArrowDown' })
    fireEvent.keyDown(textarea, { key: 'ArrowUp' })
    fireEvent.keyDown(textarea, { key: 'Tab' })
    await waitFor(() => expect(textarea.value).toBe('Antes @zara-cs depois'))
    await waitFor(() => expect(document.activeElement).toBe(textarea))
    expect(textarea.selectionStart).toBe(14)
  })

  it('seleciona explicitamente a opção ativa com Enter', async () => {
    getAgents.mockResolvedValue(agents)
    render(<CommentComposer assignee={null} submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    const textarea = screen.getByRole('combobox') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '@', selectionStart: 1 } })
    await screen.findByRole('option', { name: /@atlas-project/i })
    fireEvent.keyDown(textarea, { key: 'Enter' })
    await waitFor(() => expect(textarea.value).toBe('@atlas-project'))
  })

  it('fecha em Escape, mantém ARIA válida e aceita clique após blur', async () => {
    getAgents.mockResolvedValue(agents)
    render(<CommentComposer assignee={null} submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    const textarea = screen.getByRole('combobox') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '@', selectionStart: 1 } })
    await screen.findByRole('option', { name: /@atlas-project/i })
    expect(textarea).toHaveAttribute('aria-activedescendant', 'mention-suggestions-0')
    fireEvent.blur(textarea)
    fireEvent.click(screen.getByRole('option', { name: /@atlas-project/i }))
    await waitFor(() => expect(textarea.value).toBe('@atlas-project'))

    fireEvent.change(textarea, { target: { value: '@missing', selectionStart: 8 } })
    await waitFor(() => expect(textarea).not.toHaveAttribute('aria-activedescendant'))
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('limpa somente após submissão bem-sucedida e preserva texto no erro', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false)
    render(<CommentComposer assignee={null} submitting={false} onSubmit={onSubmit} />)
    const textarea = screen.getByRole('combobox')
    await user.type(textarea, 'primeiro')
    await user.click(screen.getByRole('button', { name: 'Comment' }))
    await waitFor(() => expect(textarea).toHaveValue(''))
    await user.type(textarea, 'erro')
    await user.click(screen.getByRole('button', { name: 'Comment' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
    expect(textarea).toHaveValue('erro')
  })

  it('separa mounts de loading, erro e vazio', async () => {
    getAgents.mockImplementation(() => new Promise(() => {}))
    const loading = render(<CommentComposer assignee={null} submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    fireEvent.change(loading.getByRole('combobox'), { target: { value: '@', selectionStart: 1 } })
    expect(await loading.findByText('Carregando agentes...')).toBeInTheDocument()
    loading.unmount()

    getAgents.mockRejectedValueOnce(new Error('offline'))
    const failed = render(<CommentComposer assignee={null} submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    fireEvent.change(failed.getByRole('combobox'), { target: { value: '@', selectionStart: 1 } })
    expect(await failed.findByText('Não foi possível carregar agentes.')).toBeInTheDocument()
    failed.unmount()

    getAgents.mockResolvedValueOnce([])
    const empty = render(<CommentComposer assignee={null} submitting={false} onSubmit={vi.fn().mockResolvedValue(false)} />)
    fireEvent.change(empty.getByRole('combobox'), { target: { value: '@', selectionStart: 1 } })
    expect(await empty.findByText('Nenhum agente disponível.')).toBeInTheDocument()
  })
})
