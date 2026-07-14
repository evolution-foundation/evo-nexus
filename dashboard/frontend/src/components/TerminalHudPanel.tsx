import { useEffect, useRef, useState } from 'react'

// Terminal HUD — gear/LCD panel (see workspace/development/features/terminal-hud).
// The gear number carries no meaning of its own — there's no fixed
// "model X = gear Y" scale to define, and none was asked for. It's the
// visual "something changed" cue: it advances whenever the backend marks a
// hud_update as `shift` (provider or model actually changed since the last
// update). The LCD readout next to it carries the real information:
// provider, model, live tokens/s.
interface HudState {
  busy: boolean
  heavy: boolean
  providerId: string
  providerModel: string
  tokensPerSec: number
  totalTokens: number | null
  bestTokensPerSec: number
  shift: boolean
}

interface Props {
  hud: HudState
  accentColor: string
}

// Fires once on the specific tick where `busy` flips true -> false — the
// moment a turn closes and the live char-based tokens/s estimate is
// replaced by the real reconciled number (see claude-bridge.js's
// _emitHudUpdate at turn close). That's the one moment a "needle settle"
// kick reads as physical rather than jittery — every other tick the number
// just drifts smoothly as text streams in, no kick needed.
function useSettleTick(busy: boolean) {
  const [tick, setTick] = useState(0)
  const prevBusy = useRef(busy)
  useEffect(() => {
    if (prevBusy.current && !busy) {
      setTick((t) => t + 1)
    }
    prevBusy.current = busy
  }, [busy])
  return tick
}

function useGear(providerId: string, providerModel: string, shift: boolean) {
  const [gear, setGear] = useState(1)
  const prevKey = useRef('')

  useEffect(() => {
    const key = `${providerId}/${providerModel}`
    if (shift && key !== prevKey.current && prevKey.current !== '') {
      setGear((g) => (g % 5) + 1)
    }
    prevKey.current = key
    // Only re-runs when the shift signal or the provider/model identity
    // itself changes — not on every hud_update tick (those fire on every
    // streamed text fragment and would otherwise re-run this on each one).
  }, [shift, providerId, providerModel])

  return gear
}

export default function TerminalHudPanel({ hud, accentColor }: Props) {
  const gear = useGear(hud.providerId, hud.providerModel, hud.shift)
  const settleTick = useSettleTick(hud.busy)
  // Sprint 3 (terminal-ux-upgrade): tokensPerSec is now a fixed
  // typical-throughput baseline per model (see claude-bridge.js's
  // _avgTokensPerSecFor), not a live-measured instantaneous rate — so
  // there's no meaningful "personal best" to flag anymore (every tick
  // trivially equals the baseline). The PB badge that used to compare
  // against bestTokensPerSec is gone; the number just reads as "médio".
  const speed = Math.max(0, Math.round(hud.tokensPerSec))

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-[#21262d] bg-[#0a0e14] px-2 py-1"
      title={`${hud.providerId}/${hud.providerModel}`}
    >
      {/* Gear indicator — digital 7-segment-ish look via a plain monospace
          numeral with a glow. `key={gear}` forces React to remount this
          element every time the gear changes, which replays the CSS
          "kick" animation below — no JS-driven setState/setTimeout needed
          just to trigger a one-shot animation. */}
      <div
        key={gear}
        className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-sm font-mono text-[13px] font-bold"
        style={{
          background: '#111826',
          color: hud.busy ? accentColor : '#4b5563',
          textShadow: hud.busy ? `0 0 6px ${accentColor}99` : 'none',
          animation: 'terminal-hud-gear-kick 260ms cubic-bezier(0.34, 1.56, 0.64, 1)',
        }}
      >
        {gear}
      </div>

      {/* LCD readout */}
      <div
        className="flex flex-col justify-center rounded-sm px-1.5 py-0.5 font-mono leading-tight"
        style={{
          background: '#04120a',
          color: '#5fffb0',
          boxShadow: 'inset 0 0 4px rgba(0,0,0,0.6)',
          // Idle "breathing" — a slow, subtle pulse instead of a static
          // display while nothing is happening. Not applied while busy —
          // that would read as "still thinking" flicker instead of calm idle.
          animation: hud.busy ? 'none' : 'terminal-hud-breathe 3.2s ease-in-out infinite',
        }}
      >
        <span className="text-[9px] tracking-wide opacity-90 truncate max-w-[110px]">
          {hud.providerId}/{hud.providerModel}
        </span>
        <span
          key={settleTick}
          className="text-[10px] font-bold tabular-nums inline-block"
          style={{ animation: 'terminal-hud-needle-settle 320ms cubic-bezier(0.34, 1.56, 0.64, 1)' }}
        >
          {speed} tok/s <span className="text-[8px] font-normal opacity-60">méd</span>
        </span>
      </div>

      <style>{`
        @keyframes terminal-hud-breathe {
          0%, 100% { opacity: 0.75; }
          50% { opacity: 1; }
        }
        @keyframes terminal-hud-gear-kick {
          0% { transform: scale(1); }
          40% { transform: scale(1.28); }
          100% { transform: scale(1); }
        }
        @keyframes terminal-hud-needle-settle {
          0% { transform: translateX(0); }
          30% { transform: translateX(2px); }
          60% { transform: translateX(-1px); }
          100% { transform: translateX(0); }
        }
      `}</style>
    </div>
  )
}
