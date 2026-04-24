import { useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'

/* ── Animated mesh background (reused from Login.tsx) ── */
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

interface WelcomeProps {
  onFirstTime: () => void
  onRestore: () => void
}

export default function Welcome({ onFirstTime, onRestore }: WelcomeProps) {
  const { t } = useTranslation()
  return (
    <div className="min-h-screen bg-[#080c14] flex items-center justify-center px-4 font-[Inter,-apple-system,sans-serif] relative">
      <NetworkCanvas />

      <div className="w-full max-w-[480px] relative z-10">
        <div className="rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]">

          {/* Header */}
          <div className="px-7 pt-7 pb-5 border-b border-[#152030]">
            <div className="flex flex-col items-center gap-3">
              <img src="/EVO_NEXUS.webp" alt="EvoNexus" className="h-8 w-auto" />
              <div className="text-center">
                <h1 className="text-[16px] font-semibold text-[#e2e8f0]">{t('onboarding.welcome.title')}</h1>
                <p className="text-[11px] text-[#4a5a6e] mt-1">{t('onboarding.welcome.subtitle')}</p>
              </div>
            </div>
          </div>

          {/* Content */}
          <div className="px-7 py-6 space-y-3">
            <p className="text-[12px] text-[#5a6b7f] text-center mb-5">
              {t('onboarding.welcome.chooseHowToStart')}
            </p>

            <button
              onClick={onFirstTime}
              className="w-full py-3 px-4 rounded-lg bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] text-sm font-semibold transition-colors"
            >
              {t('onboarding.welcome.configureFromScratch')}
            </button>

            <button
              onClick={onRestore}
              className="w-full py-3 px-4 rounded-lg border border-[#152030] text-[#5a6b7f] hover:border-[#00FFA7]/30 hover:text-[#e2e8f0] text-sm font-medium transition-colors"
            >
              {t('onboarding.welcome.restoreBrainRepo')}
            </button>

            <p className="text-[10px] text-[#2d3d4f] text-center pt-2">
              {t('onboarding.welcome.restoreHint')}
            </p>
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
