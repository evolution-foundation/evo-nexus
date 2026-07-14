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

// H-pattern gate coordinates (5-speed, no reverse) in the shifter SVG's
// 24x24 viewBox — three columns (x=6/12/18) x two rows (y=6/18), plus a
// neutral crossbar at y=12 you'd have to pass through on a real shifter.
const GATE_POS: Record<number, [number, number]> = {
  1: [6, 6], 2: [6, 18], 3: [12, 6], 4: [12, 18], 5: [18, 6],
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
  const [puckX, puckY] = GATE_POS[gear] ?? GATE_POS[1]
  const routeLabel = `${hud.providerId}/${hud.providerModel}`

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-[#21262d] bg-[#0a0e14] px-2 py-1"
      title={routeLabel}
    >
      {/* Shifter knob — H-gate diagram with a puck that slides between the
          5 positions (CSS transform transition, not a remount) plus a
          numeral badge on the corner. Reads as "gear changed" the same way
          a real shifter would, instead of just an incrementing digit. */}
      <div
        className="relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md"
        style={{
          background: 'radial-gradient(circle at 35% 30%, #1c2333, #0a0e17 70%)',
          border: '1px solid #262d3d',
          boxShadow: 'inset 0 1px 1px rgba(255,255,255,0.06), inset 0 -2px 4px rgba(0,0,0,0.5)',
        }}
      >
        <svg viewBox="0 0 24 24" className="h-full w-full p-1">
          {/* gate lines */}
          <path
            d="M6 6 V18 M12 6 V18 M18 6 V12 M6 12 H18"
            stroke="#3a4256"
            strokeWidth="1.4"
            strokeLinecap="round"
            fill="none"
          />
          {/* puck — position transitions via CSS transform, not a re-mount */}
          <circle
            r="2.4"
            fill={hud.busy ? accentColor : '#6b7280'}
            style={{
              transform: `translate(${puckX}px, ${puckY}px)`,
              transition: 'transform 280ms cubic-bezier(0.34, 1.56, 0.64, 1)',
              filter: hud.busy ? `drop-shadow(0 0 3px ${accentColor})` : 'none',
            }}
          />
        </svg>
        {/* Gear numeral badge — `key={gear}` replays the kick animation on
            every shift, same trick the old plain-numeral version used. */}
        <div
          key={gear}
          className="absolute -top-1.5 -left-1.5 flex h-4 w-4 items-center justify-center rounded-full font-mono text-[9px] font-bold"
          style={{
            background: '#111826',
            border: '1px solid #262d3d',
            color: hud.busy ? accentColor : '#8b95a8',
            textShadow: hud.busy ? `0 0 5px ${accentColor}99` : 'none',
            animation: 'terminal-hud-gear-kick 260ms cubic-bezier(0.34, 1.56, 0.64, 1)',
          }}
        >
          {gear}
        </div>
      </div>

      {/* LCD readout — full route label, no more aggressive truncation
          now that this panel has its own full-width dashboard row. */}
      <div
        className="flex min-w-0 flex-col justify-center rounded-sm px-1.5 py-0.5 font-mono leading-tight"
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
        <span className="text-[9px] tracking-wide opacity-90 truncate max-w-[65vw] sm:max-w-[320px]">
          {routeLabel}
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
