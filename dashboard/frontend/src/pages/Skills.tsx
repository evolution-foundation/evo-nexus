import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Zap,
  Search,
  Layers,
  Mail,
  BarChart3,
  Globe,
  MessageSquare,
  Compass,
  Heart,
  DollarSign,
  Megaphone,
  BookOpen,
  Activity,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import { useTranslation } from 'react-i18next'

interface Skill {
  name: string
  prefix: string
  description: string
  path: string
}

// Category styling and icons
const CATEGORY_META: Record<string, { icon: LucideIcon; color: string; colorMuted: string; glowColor: string; label: string }> = {
  'social': { icon: Globe, color: '#A78BFA', colorMuted: 'rgba(167,139,250,0.12)', glowColor: 'rgba(167,139,250,0.15)', label: 'Social Media' },
  'int': { icon: Layers, color: '#60A5FA', colorMuted: 'rgba(96,165,250,0.12)', glowColor: 'rgba(96,165,250,0.15)', label: 'Integrations' },
  'fin': { icon: DollarSign, color: '#34D399', colorMuted: 'rgba(52,211,153,0.12)', glowColor: 'rgba(52,211,153,0.15)', label: 'Finance' },
  'prod': { icon: Activity, color: '#22D3EE', colorMuted: 'rgba(34,211,238,0.12)', glowColor: 'rgba(34,211,238,0.15)', label: 'Productivity' },
  'mkt': { icon: Megaphone, color: '#FB923C', colorMuted: 'rgba(251,146,60,0.12)', glowColor: 'rgba(251,146,60,0.15)', label: 'Marketing' },
  'gog': { icon: Mail, color: '#F472B6', colorMuted: 'rgba(244,114,182,0.12)', glowColor: 'rgba(244,114,182,0.15)', label: 'Google' },
  'obs': { icon: BookOpen, color: '#818CF8', colorMuted: 'rgba(129,140,248,0.12)', glowColor: 'rgba(129,140,248,0.15)', label: 'Obsidian' },
  'discord': { icon: MessageSquare, color: '#7C8AFF', colorMuted: 'rgba(124,138,255,0.12)', glowColor: 'rgba(124,138,255,0.15)', label: 'Discord' },
  'pulse': { icon: Heart, color: '#2DD4BF', colorMuted: 'rgba(45,212,191,0.12)', glowColor: 'rgba(45,212,191,0.15)', label: 'Community' },
  'sage': { icon: Compass, color: '#FBBF24', colorMuted: 'rgba(251,191,36,0.12)', glowColor: 'rgba(251,191,36,0.15)', label: 'Strategy' },
  'evo': { icon: BarChart3, color: '#00FFA7', colorMuted: 'rgba(0,255,167,0.12)', glowColor: 'rgba(0,255,167,0.15)', label: 'Evo Method' },
}

const DEFAULT_CATEGORY = { icon: Zap, color: '#8b949e', colorMuted: 'rgba(139,148,158,0.12)', glowColor: 'rgba(139,148,158,0.15)', label: 'Other' }

function getCategoryMeta(prefix: string) {
  // Try exact match first, then prefix match
  if (CATEGORY_META[prefix]) return CATEGORY_META[prefix]
  const key = Object.keys(CATEGORY_META).find((k) => prefix.startsWith(k))
  return key ? CATEGORY_META[key] : DEFAULT_CATEGORY
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-[#21262d] bg-[#161b22] p-5">
      <div className="flex items-start justify-between mb-3">
        <div className="h-10 w-10 rounded-lg bg-[#21262d] animate-pulse" />
        <div className="h-5 w-16 rounded-full bg-[#21262d] animate-pulse" />
      </div>
      <div className="h-4 w-32 rounded bg-[#21262d] animate-pulse mb-2" />
      <div className="space-y-1.5 mb-4">
        <div className="h-3 w-full rounded bg-[#21262d] animate-pulse" />
        <div className="h-3 w-2/3 rounded bg-[#21262d] animate-pulse" />
      </div>
    </div>
  )
}

function SkeletonStat() {
  return <div className="skeleton h-24 rounded-2xl" />
}

function StatCard({ label, value, icon: Icon }: { label: string; value: string | number; icon: LucideIcon }) {
  return (
    <div className="group relative bg-[#161b22] border border-[#21262d] rounded-2xl p-5 transition-all duration-300 hover:border-[#00FFA7]/40 hover:shadow-[0_0_24px_rgba(0,255,167,0.06)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#00FFA7]/20 to-transparent rounded-t-2xl" />
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-[#00FFA7]/8 border border-[#00FFA7]/15">
          <Icon size={18} className="text-[#00FFA7]" />
        </div>
      </div>
      <p className="text-3xl font-bold text-[#e6edf3] tracking-tight">{value}</p>
      <p className="text-sm text-[#667085] mt-1">{label}</p>
    </div>
  )
}

function SkillCard({ skill }: { skill: Skill }) {
  const meta = getCategoryMeta(skill.prefix)
  const Icon = meta.icon

  return (
    <Link
      to={`/skills/${skill.name}`}
      className="group relative block rounded-xl border border-[#21262d] bg-[#161b22] p-5 transition-all duration-300 hover:border-transparent"
    >
      {/* Hover glow effect */}
      <div
        className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          boxShadow: `inset 0 0 0 1px ${meta.color}44, 0 0 20px ${meta.glowColor}`,
          borderRadius: 'inherit',
        }}
      />

      {/* Top row: icon + category badge */}
      <div className="relative flex items-start justify-between mb-3">
        <div
          className="flex h-10 w-10 items-center justify-center rounded-lg transition-transform duration-300 group-hover:scale-110"
          style={{ backgroundColor: meta.colorMuted }}
        >
          <Icon size={20} style={{ color: meta.color }} />
        </div>
        <span
          className="text-[10px] font-medium uppercase tracking-wider px-2 py-0.5 rounded-full border"
          style={{
            backgroundColor: meta.colorMuted,
            color: meta.color,
            borderColor: `${meta.color}33`,
          }}
        >
          {meta.label}
        </span>
      </div>

      {/* Name */}
      <div className="relative mb-1.5">
        <h3 className="text-[15px] font-semibold text-[#e6edf3] transition-colors duration-200 group-hover:text-white">
          {skill.name}
        </h3>
        <code className="mt-1 inline-block text-[11px] font-mono text-[#667085]">
          {skill.prefix}-*
        </code>
      </div>

      {/* Description */}
      <p className="relative text-[13px] leading-relaxed text-[#667085] line-clamp-2">
        {skill.description || 'No description available.'}
      </p>
    </Link>
  )
}

export default function Skills() {
  const { t } = useTranslation()
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  useEffect(() => {
    api.get('/skills')
      .then((data) => {
        const skillsList: Skill[] = Array.isArray(data) ? data : (data?.skills || [])
        setSkills(skillsList)
      })
      .catch(() => setSkills([]))
      .finally(() => setLoading(false))
  }, [])

  const filtered = skills.filter((s) => {
    const matchesSearch = !search || s.name.toLowerCase().includes(search.toLowerCase()) || s.description.toLowerCase().includes(search.toLowerCase())
    const matchesCategory = !selectedCategory || s.prefix === selectedCategory
    return matchesSearch && matchesCategory
  })

  const categories = [...new Set(skills.map((s) => s.prefix))].sort()

  return (
    <div className="max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-[#e6edf3] tracking-tight">{t('skills.title')}</h1>
        <p className="text-[#667085] text-sm mt-1">Specialized capabilities and domain knowledge</p>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
        {loading ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : (
          <>
            <StatCard label="Total Skills" value={skills.length} icon={Zap} />
            <StatCard label="Categories" value={categories.length} icon={Layers} />
          </>
        )}
      </div>

      {/* Search + Filter Bar */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#667085]" />
          <input
            type="text"
            placeholder="Search skills..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-[#161b22] border border-[#21262d] rounded-xl pl-9 pr-4 py-2.5 text-sm text-[#e6edf3] placeholder-[#667085] focus:outline-none focus:border-[#00FFA7]/50 focus:shadow-[0_0_12px_rgba(0,255,167,0.08)] transition-all"
          />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => setSelectedCategory(null)}
            className={`text-xs px-3 py-1.5 rounded-full border transition-all duration-200 ${
              !selectedCategory
                ? 'bg-[#00FFA7]/10 text-[#00FFA7] border-[#00FFA7]/30'
                : 'bg-transparent text-[#667085] border-[#21262d] hover:border-[#667085]/50'
            }`}
          >
            All
          </button>
          {categories.map((cat) => {
            const meta = getCategoryMeta(cat)
            const isActive = selectedCategory === cat
            return (
              <button
                key={cat}
                onClick={() => setSelectedCategory(isActive ? null : cat)}
                className="text-xs px-3 py-1.5 rounded-full border transition-all duration-200"
                style={{
                  backgroundColor: isActive ? meta.colorMuted : 'transparent',
                  color: isActive ? meta.color : '#667085',
                  borderColor: isActive ? `${meta.color}44` : '#21262d',
                }}
              >
                {cat}
              </button>
            )
          })}
        </div>
      </div>

      {/* Skills Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(9)].map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#161b22] border border-[#21262d]">
            <Zap size={32} className="text-[#3F3F46]" />
          </div>
          <p className="text-[#667085] text-lg">No skills found</p>
          <p className="text-[#3F3F46] text-sm mt-1">Try adjusting your search or filter</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((skill) => (
            <SkillCard key={skill.name} skill={skill} />
          ))}
        </div>
      )}
    </div>
  )
}
