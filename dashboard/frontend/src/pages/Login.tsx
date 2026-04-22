import { useState, useEffect, useRef, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../context/AuthContext'

/* ── Animated mesh background ── */
function NetworkCanvas() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let animId: number
    let particles: { x: number; y: number; vx: number; vy: number }[] = []

    const resize = () => {
      canvas.width = window.innerWidth
      canvas.height = window.innerHeight
    }

    const init = () => {
      resize()
      const count = Math.floor((canvas.width * canvas.height) / 18000)
      particles = Array.from({ length: Math.min(count, 80) }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
      }))
    }

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      const maxDist = 150

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0 || p.x > canvas.width) p.vx *= -1
        if (p.y < 0 || p.y > canvas.height) p.vy *= -1

        ctx.beginPath()
        ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(0, 255, 167, 0.25)'
        ctx.fill()

        for (let j = i + 1; j < particles.length; j++) {
          const q = particles[j]
          const dx = p.x - q.x
          const dy = p.y - q.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < maxDist) {
            ctx.beginPath()
            ctx.moveTo(p.x, p.y)
            ctx.lineTo(q.x, q.y)
            ctx.strokeStyle = `rgba(0, 255, 167, ${0.06 * (1 - dist / maxDist)})`
            ctx.lineWidth = 0.5
            ctx.stroke()
          }
        }
      }
      animId = requestAnimationFrame(draw)
    }

    init()
    draw()
    window.addEventListener('resize', init)
    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('resize', init)
    }
  }, [])

  return <canvas ref={ref} className="fixed inset-0 pointer-events-none" style={{ zIndex: 0 }} />
}

export default function Login() {
  const { t } = useTranslation()
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!username.trim() || !password) {
      setError('Username and password are required')
      return
    }
    setSubmitting(true)
    try {
      await login(username.trim(), password)
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  const inp = "w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm transition-colors duration-200 focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20"
  const lbl = "block text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase"

  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif] relative">
      <NetworkCanvas />

      <div className="w-full max-w-[380px] relative z-10">
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">

          {/* Header */}
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <div className="flex flex-col items-center gap-3">
              <img src="/EVO_NEXUS.webp" alt="EvoNexus" className="h-8 w-auto" />
              <p className="text-[11px] text-[#4a5a6e]">{t('login.subtitle')}</p>
            </div>
          </div>

          {/* Form */}
          <div className="px-7 py-6">
            {error && (
              <div className="mb-4 px-3 py-2.5 rounded-lg bg-[#1a0a0a] border border-[#3a1515] text-[#f87171] text-xs">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className={lbl}>{t('login.username')}</label>
                <input type="text" value={username} onChange={e => setUsername(e.target.value)}
                  className={inp} placeholder={t('login.username')} autoFocus autoComplete="username" />
              </div>
              <div>
                <label className={lbl}>{t('login.password')}</label>
                <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                  className={inp} placeholder={t('login.password')} autoComplete="current-password" />
              </div>

              <button type="submit" disabled={submitting}
                className={`w-full py-3 mt-1 rounded-lg text-sm font-semibold transition-colors disabled:opacity-40 ${
                  submitting
                    ? 'bg-[#00FFA7]/60 text-[#080c14]'
                    : 'bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] active:bg-[#00cc88]'
                }`}>
                {submitting ? t('login.signingIn') : t('login.submit')}
              </button>
            </form>
          </div>
        </div>

        <p className="text-center mt-4 text-[10px] text-[#2d3d4f]">
          <a href="https://evolutionfoundation.com.br" target="_blank" rel="noopener noreferrer"
            className="hover:text-[#4a5a6e] transition-colors">
            Evolution Foundation
          </a>
        </p>
      </div>
    </div>
  )
}
