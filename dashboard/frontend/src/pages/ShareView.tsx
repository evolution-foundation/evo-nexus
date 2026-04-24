import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import Markdown from '../components/Markdown'
import CodeView from '../components/workspace/CodeView'

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080' : ''

type ViewState =
  | { status: 'loading' }
  | { status: 'html'; content: string }
  | { status: 'markdown'; content: string }
  | { status: 'code'; content: string; extension: string }
  | { status: 'text'; content: string }
  | { status: 'image'; url: string; mime: string }
  | { status: 'video'; url: string; mime: string }
  | { status: 'audio'; url: string; mime: string }
  | { status: 'pdf'; url: string }
  | { status: 'error'; message: string }

export default function ShareView() {
  const { token } = useParams<{ token: string }>()
  const [state, setState] = useState<ViewState>({ status: 'loading' })

  useEffect(() => {
    if (!token) {
      setState({ status: 'error', message: 'Token inválido.' })
      return
    }

    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/shares/${token}/view`)

        if (!res.ok) {
          setState({ status: 'error', message: 'Este link expirou ou não está mais disponível.' })
          return
        }

        const contentType = res.headers.get('content-type') || ''

        // HTML: served raw — we have the raw text, render it
        if (contentType.includes('text/html')) {
          const html = await res.text()
          setState({ status: 'html', content: html })
          return
        }

        // Images: binary response
        if (contentType.startsWith('image/')) {
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          setState({ status: 'image', url, mime: contentType })
          return
        }

        // Video: binary response
        if (contentType.startsWith('video/')) {
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          setState({ status: 'video', url, mime: contentType })
          return
        }

        // Audio: binary response
        if (contentType.startsWith('audio/')) {
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          setState({ status: 'audio', url, mime: contentType })
          return
        }

        // PDF: binary response
        if (contentType.includes('application/pdf')) {
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          setState({ status: 'pdf', url })
          return
        }

        // JSON response for markdown/code/text
        const data = await res.json()
        if (data.type === 'markdown') {
          setState({ status: 'markdown', content: data.content })
        } else if (data.type === 'code') {
          setState({ status: 'code', content: data.content, extension: data.extension || '' })
        } else {
          setState({ status: 'text', content: data.content })
        }
      } catch {
        setState({ status: 'error', message: 'Erro ao carregar o arquivo compartilhado.' })
      }
    }

    load()
  }, [token])

  // Full-page HTML rendering — use iframe to isolate CSS
  if (state.status === 'html') {
    return (
      <div style={{ position: 'relative', minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <iframe
          srcDoc={state.content}
          style={{
            flex: 1,
            width: '100%',
            minHeight: 'calc(100vh - 40px)',
            border: 'none',
            background: '#0C111D',
          }}
          title="Arquivo compartilhado"
          sandbox="allow-same-origin allow-scripts"
        />
        <PoweredByFooter />
      </div>
    )
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--bg-primary, #0C111D)',
        color: 'var(--text-primary, #D0D5DD)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {state.status === 'loading' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh' }}>
            <span style={{ color: 'var(--text-muted, #667085)', fontSize: '14px' }}>Carregando...</span>
          </div>
        )}

        {state.status === 'error' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh' }}>
            <div style={{ textAlign: 'center', maxWidth: '400px', padding: '0 24px' }}>
              <div
                style={{
                  width: '56px',
                  height: '56px',
                  borderRadius: '16px',
                  background: 'var(--bg-card, #182230)',
                  border: '1px solid var(--border, #344054)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  margin: '0 auto 16px',
                }}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted, #667085)" strokeWidth="1.5">
                  <path d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <p style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary, #D0D5DD)', marginBottom: '8px' }}>
                Link indisponível
              </p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted, #667085)', lineHeight: 1.6 }}>
                {state.message}
              </p>
            </div>
          </div>
        )}

        {state.status === 'markdown' && (
          <div style={{ maxWidth: '860px', margin: '0 auto', padding: '32px 24px 80px' }}>
            <Markdown>{state.content}</Markdown>
          </div>
        )}

        {state.status === 'code' && (
          <div style={{ padding: '24px 24px 80px', height: 'calc(100vh - 48px)' }}>
            <CodeView content={state.content} path={`.${state.extension}`} />
          </div>
        )}

        {state.status === 'text' && (
          <div style={{ maxWidth: '860px', margin: '0 auto', padding: '32px 24px 80px' }}>
            <pre
              style={{
                fontSize: '13px',
                lineHeight: 1.7,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: 'var(--text-secondary, #98A2B3)',
                fontFamily: 'monospace',
              }}
            >
              {state.content}
            </pre>
          </div>
        )}

        {state.status === 'image' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '32px 24px 80px', minHeight: '60vh' }}>
            <img
              src={state.url}
              alt="Arquivo compartilhado"
              style={{ maxWidth: '100%', maxHeight: '80vh', borderRadius: '8px', objectFit: 'contain' }}
            />
          </div>
        )}

        {state.status === 'video' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '32px 24px 80px', minHeight: '60vh' }}>
            <video
              controls
              autoPlay={false}
              style={{ maxWidth: '100%', maxHeight: '80vh', borderRadius: '8px' }}
            >
              <source src={state.url} type={state.mime} />
              Seu navegador não suporta a reprodução de vídeo.
            </video>
          </div>
        )}

        {state.status === 'audio' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '32px 24px 80px', minHeight: '60vh', gap: '24px' }}>
            <div
              style={{
                width: '80px',
                height: '80px',
                borderRadius: '20px',
                background: 'var(--bg-card, #182230)',
                border: '1px solid var(--border, #344054)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--evo-green, #00FFA7)" strokeWidth="1.5">
                <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
              </svg>
            </div>
            <audio controls style={{ width: '100%', maxWidth: '500px' }}>
              <source src={state.url} type={state.mime} />
              Seu navegador não suporta a reprodução de áudio.
            </audio>
          </div>
        )}

        {state.status === 'pdf' && (
          <div style={{ padding: '0', height: 'calc(100vh - 48px)' }}>
            <iframe
              src={state.url}
              style={{ width: '100%', height: '100%', border: 'none' }}
              title="PDF compartilhado"
            />
          </div>
        )}
      </div>

      {/* Footer branding */}
      <PoweredByFooter />
    </div>
  )
}

function PoweredByFooter() {
  return (
    <a
      href="https://evonexus.evolutionfoundation.com.br/"
      target="_blank"
      rel="noopener noreferrer"
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        height: '40px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        background: 'rgba(10,15,26,0.92)',
        backdropFilter: 'blur(8px)',
        borderTop: '1px solid rgba(52,64,84,0.5)',
        zIndex: 100,
        textDecoration: 'none',
        cursor: 'pointer',
      }}
    >
      <img src="/EVO_NEXUS.webp" alt="EvoNexus" style={{ height: '16px', width: 'auto', opacity: 0.7 }} />
      <span style={{ fontSize: '11px', color: 'var(--text-muted, #667085)' }}>
        Powered by EvoNexus
      </span>
    </a>
  )
}
