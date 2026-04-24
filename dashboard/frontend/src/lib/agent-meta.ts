import {
  Bot,
  Brain,
  FolderKanban,
  DollarSign,
  Heart,
  GraduationCap,
  Target,
  Camera,
  Users,
  Compass,
  BookOpen,
  Megaphone,
  UserCheck,
  Headphones,
  Scale,
  Lightbulb,
  BarChart3,
  Navigation,
  History,
  Zap,
  type LucideIcon,
} from 'lucide-react'

export interface AgentMeta {
  icon: LucideIcon
  color: string
  command: string
  label: string
  avatar?: string
  // Wave 2.0: plugin agents may declare an avatar_url served by the backend
  avatar_url?: string | null
}

// ---------------------------------------------------------------------------
// Static seed — 38 native agents (pre-hydration fallback, covers 100% of
// natives before the first fetch completes). Contract: getAgentMeta() is
// always synchronous.
// ---------------------------------------------------------------------------
const AGENT_META_SEED: Record<string, AgentMeta> = {
  'atlas-project': { icon: FolderKanban, color: '#60A5FA', command: '/atlas-project', label: 'Projects', avatar: '/avatar/avatar_atlas.webp' },
  'clawdia-assistant': { icon: Brain, color: '#22D3EE', command: '/clawdia', label: 'Operations', avatar: '/avatar/avatar_clawdia.webp' },
  'flux-finance': { icon: DollarSign, color: '#34D399', command: '/flux', label: 'Finance', avatar: '/avatar/avatar_flux.webp' },
  'kai-personal-assistant': { icon: Heart, color: '#F472B6', command: '/kai', label: 'Personal', avatar: '/avatar/avatar_kai.webp' },
  'mentor-courses': { icon: GraduationCap, color: '#FBBF24', command: '/mentor', label: 'Courses', avatar: '/avatar/avatar_mentor.webp' },
  'lumen-learning': { icon: Zap, color: '#FCD34D', command: '/lumen-learning', label: 'Learning Retention', avatar: '/avatar/avatar_lumen.webp' },
  'nex-sales': { icon: Target, color: '#FB923C', command: '/nex', label: 'Sales', avatar: '/avatar/avatar_nex.webp' },
  'pixel-social-media': { icon: Camera, color: '#A78BFA', command: '/pixel', label: 'Social Media', avatar: '/avatar/avatar_pixel.webp' },
  'pulse-community': { icon: Users, color: '#2DD4BF', command: '/pulse', label: 'Community', avatar: '/avatar/avatar_pulse.webp' },
  'sage-strategy': { icon: Compass, color: '#818CF8', command: '/sage', label: 'Strategy', avatar: '/avatar/avatar_sage.webp' },
  oracle: { icon: BookOpen, color: '#F59E0B', command: '/oracle', label: 'Knowledge', avatar: '/avatar/avatar_oracle.webp' },
  'mako-marketing': { icon: Megaphone, color: '#FB923C', command: '/mako', label: 'Marketing', avatar: '/avatar/avatar_mako.webp' },
  'aria-hr': { icon: UserCheck, color: '#F472B6', command: '/aria', label: 'HR / People', avatar: '/avatar/avatar_aria.webp' },
  'zara-cs': { icon: Headphones, color: '#22D3EE', command: '/zara', label: 'Customer Success', avatar: '/avatar/avatar_zara.webp' },
  'lex-legal': { icon: Scale, color: '#C084FC', command: '/lex', label: 'Legal', avatar: '/avatar/avatar_lex.webp' },
  'nova-product': { icon: Lightbulb, color: '#60A5FA', command: '/nova', label: 'Product', avatar: '/avatar/avatar_nova.webp' },
  'dex-data': { icon: BarChart3, color: '#FBBF24', command: '/dex', label: 'Data / BI', avatar: '/avatar/avatar_dex.webp' },
  'helm-conductor': { icon: Navigation, color: '#14B8A6', command: '/helm-conductor', label: 'Cycle Orchestration', avatar: '/avatar/avatar_helm.webp' },
  'mirror-retro': { icon: History, color: '#94A3B8', command: '/mirror-retro', label: 'Retrospective', avatar: '/avatar/avatar_mirror.webp' },
  'apex-architect': { icon: Bot, color: '#A78BFA', command: '/apex-architect', label: 'Architect', avatar: '/avatar/avatar_apex.webp' },
  'bolt-executor': { icon: Bot, color: '#FCD34D', command: '/bolt-executor', label: 'Executor', avatar: '/avatar/avatar_bolt.webp' },
  'canvas-designer': { icon: Bot, color: '#F472B6', command: '/canvas-designer', label: 'Designer', avatar: '/avatar/avatar_canvas.webp' },
  'compass-planner': { icon: Bot, color: '#60A5FA', command: '/compass-planner', label: 'Planner', avatar: '/avatar/avatar_compass.webp' },
  'echo-analyst': { icon: Bot, color: '#22D3EE', command: '/echo-analyst', label: 'Analyst', avatar: '/avatar/avatar_echo.webp' },
  'flow-git': { icon: Bot, color: '#34D399', command: '/flow-git', label: 'Git Master', avatar: '/avatar/avatar_flow.webp' },
  'grid-tester': { icon: Bot, color: '#FBBF24', command: '/grid-tester', label: 'Test Engineer', avatar: '/avatar/avatar_grid.webp' },
  'hawk-debugger': { icon: Bot, color: '#FB923C', command: '/hawk-debugger', label: 'Debugger', avatar: '/avatar/avatar_hawk.webp' },
  'lens-reviewer': { icon: Bot, color: '#C084FC', command: '/lens-reviewer', label: 'Code Reviewer', avatar: '/avatar/avatar_lens.webp' },
  'oath-verifier': { icon: Bot, color: '#2DD4BF', command: '/oath-verifier', label: 'Verifier', avatar: '/avatar/avatar_oath.webp' },
  'prism-scientist': { icon: Bot, color: '#818CF8', command: '/prism-scientist', label: 'Scientist', avatar: '/avatar/avatar_prism.webp' },
  'probe-qa': { icon: Bot, color: '#F59E0B', command: '/probe-qa', label: 'QA Tester', avatar: '/avatar/avatar_probe.webp' },
  'quill-writer': { icon: Bot, color: '#94A3B8', command: '/quill-writer', label: 'Writer', avatar: '/avatar/avatar_quill.webp' },
  'raven-critic': { icon: Bot, color: '#F87171', command: '/raven-critic', label: 'Critic', avatar: '/avatar/avatar_raven.webp' },
  'scout-explorer': { icon: Bot, color: '#22D3EE', command: '/scout-explorer', label: 'Explorer', avatar: '/avatar/avatar_scout.webp' },
  'scroll-docs': { icon: Bot, color: '#FCD34D', command: '/scroll-docs', label: 'Document Specialist', avatar: '/avatar/avatar_scroll.webp' },
  'trail-tracer': { icon: Bot, color: '#34D399', command: '/trail-tracer', label: 'Tracer', avatar: '/avatar/avatar_trail.webp' },
  'vault-security': { icon: Bot, color: '#F87171', command: '/vault-security', label: 'Security Reviewer', avatar: '/avatar/avatar_vault.webp' },
  'zen-simplifier': { icon: Bot, color: '#A78BFA', command: '/zen-simplifier', label: 'Code Simplifier', avatar: '/avatar/avatar_zen.webp' },
}

// ---------------------------------------------------------------------------
// Wave 2.0: runtime registry — starts as a copy of the seed; hydrated once
// per session via hydrateAgentMeta(). Merge is additive: seed entries for
// native agents are never removed.
// ---------------------------------------------------------------------------

// Module-level mutable registry (not exported — callers use getAgentMeta).
let _registry: Record<string, AgentMeta> = { ...AGENT_META_SEED }
let _hydrated = false

const DEFAULT_META: AgentMeta = {
  icon: Bot,
  color: '#00FFA7',
  command: '',
  label: 'Agent',
}

/**
 * Fetch /api/agent-meta and merge plugin agents into the local registry.
 *
 * - Idempotent: second call is a no-op unless `force` is true.
 * - Never destroys the seed: native agent entries are kept even on fetch error.
 * - Plugin agents gain `avatar_url` from the backend response.
 */
export async function hydrateAgentMeta(force = false): Promise<void> {
  if (_hydrated && !force) return
  try {
    const API = import.meta.env.DEV ? 'http://localhost:8080' : ''
    const res = await fetch(`${API}/api/agent-meta`, { credentials: 'include' })
    if (!res.ok) return  // silently keep seed on non-2xx
    const data: Record<string, { label: string; avatar_url: string | null }> = await res.json()
    for (const [slug, entry] of Object.entries(data)) {
      const existing = _registry[slug]
      if (existing) {
        // Native agent: update avatar_url (and avatar for AgentAvatar.tsx compat) if backend provides one
        _registry[slug] = {
          ...existing,
          avatar_url: entry.avatar_url ?? existing.avatar_url,
          avatar: entry.avatar_url ?? existing.avatar,
        }
      } else {
        // Plugin agent: synthesize a new entry with defaults for icon/color/command.
        // Set both avatar_url (Wave 2.0 field) and avatar (AgentAvatar.tsx reads this).
        _registry[slug] = {
          icon: Bot,
          color: '#00FFA7',
          command: `/${slug}`,
          label: entry.label || slug,
          avatar_url: entry.avatar_url,
          avatar: entry.avatar_url ?? undefined,
        }
      }
    }
    _hydrated = true
  } catch {
    // Network error — keep seed, don't set _hydrated so next call retries
  }
}

/**
 * Synchronous agent meta lookup.
 *
 * Returns the seed entry (or hydrated entry) for native agents.
 * Returns a synthesized entry for plugin agents after hydration.
 * Falls back to DEFAULT_META for unknown slugs.
 *
 * Contract: always synchronous. Callers in hot render paths (AgentAvatar,
 * AgentChat) are unaffected.
 */
export function getAgentMeta(name: string): AgentMeta {
  const base = _registry[name] || DEFAULT_META
  return { ...base, command: _registry[name]?.command || `/${name}` }
}
