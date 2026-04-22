/**
 * i18n bootstrap for the EvoNexus dashboard.
 *
 * Resolution order for the active locale:
 *   1. `workspace.language` from the backend config (applied by AuthContext
 *      via `setWorkspaceLanguage` below once it loads)
 *   2. `localStorage.evo_lang` (a previous user choice persists across
 *      sessions even when the workspace config is unreachable)
 *   3. `navigator.language` (best-effort browser preference)
 *   4. `en-US` fallback
 *
 * Translations live in `locales/<bcp47>/<namespace>.json`. Unknown codes
 * (e.g. `pt-PT`, `ja`) fall back to `en-US` — add a new folder under
 * `locales/` to ship a new language.
 */

import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'

import ptBR from './locales/pt-BR'
import enUS from './locales/en-US'
import es from './locales/es'

export const SUPPORTED_LOCALES = ['pt-BR', 'en-US', 'es'] as const
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]

export const DEFAULT_LOCALE: SupportedLocale = 'en-US'

/**
 * Normalize legacy / short codes to a supported BCP-47 tag.
 * Examples:
 *   'ptBR'  → 'pt-BR'       (legacy setup.py format)
 *   'pt'    → 'pt-BR'       (fall back to our only Portuguese variant)
 *   'en'    → 'en-US'
 *   'en-GB' → 'en-US'       (fall back until we ship en-GB)
 *   'ja'    → 'en-US'       (unsupported → DEFAULT_LOCALE)
 */
export function normalizeLocale(raw: string | null | undefined): SupportedLocale {
  if (!raw) return DEFAULT_LOCALE
  const cleaned = String(raw).trim().replace('_', '-')

  // Exact match wins
  if ((SUPPORTED_LOCALES as readonly string[]).includes(cleaned)) {
    return cleaned as SupportedLocale
  }

  // Legacy "ptBR" without hyphen
  if (/^ptBR$/i.test(cleaned)) return 'pt-BR'
  if (/^enUS$/i.test(cleaned)) return 'en-US'

  // Language-only codes → pick the regional variant we ship
  const lang = cleaned.toLowerCase().split('-')[0]
  if (lang === 'pt') return 'pt-BR'
  if (lang === 'en') return 'en-US'
  if (lang === 'es') return 'es'

  return DEFAULT_LOCALE
}

const resources = {
  'pt-BR': ptBR,
  'en-US': enUS,
  es,
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: SUPPORTED_LOCALES as unknown as string[],
    nonExplicitSupportedLngs: true,
    load: 'currentOnly',
    interpolation: {
      escapeValue: false, // React already escapes
    },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'evo_lang',
      caches: ['localStorage'],
    },
    returnNull: false,
  })

/**
 * Called by AuthContext once `workspace.language` is loaded from the
 * backend. Takes priority over any language the detector picked up.
 */
export function setWorkspaceLanguage(raw: string | null | undefined): void {
  const normalized = normalizeLocale(raw)
  if (i18n.language !== normalized) {
    i18n.changeLanguage(normalized)
  }
  try {
    localStorage.setItem('evo_lang', normalized)
  } catch {
    // private mode / storage disabled — not fatal
  }
}

export default i18n
