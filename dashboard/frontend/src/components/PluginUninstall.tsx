/**
 * PluginUninstall — B3 safe_uninstall 3-step wizard.
 *
 * Shown instead of window.confirm() when the plugin manifest declares
 * safe_uninstall.enabled: true.
 *
 * Step 1 — Regulatory reason + "I accept responsibility" checkbox.
 * Step 2 — ZIP password input (Vault B3.S5: AES-256 export encryption).
 * Step 3 — Typed confirmation phrase + Uninstall button.
 *
 * For plugins without safe_uninstall (or safe_uninstall.enabled: false),
 * render nothing — the caller falls back to the legacy window.confirm() path.
 *
 * Force-uninstall banner: if EVONEXUS_ALLOW_FORCE_UNINSTALL=1 is detected
 * in the API response, a persistent orange alert is shown.
 */

import { useState } from 'react'
import { AlertTriangle, Lock, Shield, Trash2, X } from 'lucide-react'
import { api } from '../lib/api'

export interface SafeUninstallSpec {
  enabled?: boolean
  block_uninstall?: boolean
  reason?: string
  user_confirmation?: {
    checkbox_label?: string
    typed_phrase?: string
  }
  pre_uninstall_hook?: {
    script?: string
    output_dir?: string
    timeout_seconds?: number
    must_produce_file?: boolean
  }
  preserved_tables?: string[]
}

interface Props {
  slug: string
  safeUninstall: SafeUninstallSpec
  forceUninstallActive?: boolean
  onClose: () => void
  onUninstalled: () => void
}

type Step = 1 | 2 | 3

export default function PluginUninstall({
  slug,
  safeUninstall,
  forceUninstallActive = false,
  onClose,
  onUninstalled,
}: Props) {
  const [step, setStep] = useState<Step>(1)
  const [checkboxChecked, setCheckboxChecked] = useState(false)
  const [zipPassword, setZipPassword] = useState('')
  const [zipPasswordConfirm, setZipPasswordConfirm] = useState('')
  const [typedPhrase, setTypedPhrase] = useState('')
  const [uninstalling, setUninstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const requiredPhrase = safeUninstall?.user_confirmation?.typed_phrase ?? ''
  const checkboxLabel =
    safeUninstall?.user_confirmation?.checkbox_label ??
    'Tenho uma cópia dos dados exportados e assumo responsabilidade pela retenção legal.'
  const reason = safeUninstall?.reason ?? ''
  const preservedTables = safeUninstall?.preserved_tables ?? []

  const phraseMatches = typedPhrase === requiredPhrase
  const passwordsMatch = zipPassword === zipPasswordConfirm && zipPassword.length >= 8

  async function handleUninstall() {
    setUninstalling(true)
    setError(null)
    try {
      const body: Record<string, unknown> = {
        confirmation_phrase: typedPhrase,
        zip_password: zipPassword,
      }
      await api.delete(`/plugins/${slug}`, body)
      onUninstalled()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unexpected error during uninstall.')
      setUninstalling(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="relative w-full max-w-lg rounded-xl border border-neutral-800 bg-neutral-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-800 px-6 py-4">
          <div className="flex items-center gap-2 text-red-400">
            <Trash2 className="h-5 w-5" />
            <span className="font-semibold">Desinstalar plugin: {slug}</span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-neutral-400 hover:text-white"
            aria-label="Fechar"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Force-uninstall alert */}
        {forceUninstallActive && (
          <div className="mx-6 mt-4 rounded border border-orange-500 bg-orange-950 px-4 py-3 text-sm text-orange-200">
            <strong>⚠ Force uninstall ATIVO — todas proteções desabilitadas</strong>
            <p className="mt-1 text-orange-300">
              EVONEXUS_ALLOW_FORCE_UNINSTALL=1 está definido. Esta ação ignora a confirmação e
              preservação de dados. Todas as ações são auditadas.
            </p>
          </div>
        )}

        <div className="space-y-5 px-6 py-5">
          {/* Step indicator */}
          <div className="flex items-center gap-2 text-xs text-neutral-500">
            {([1, 2, 3] as Step[]).map((s) => (
              <span
                key={s}
                className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold ${
                  step === s
                    ? 'bg-[#00FFA7] text-black'
                    : step > s
                    ? 'bg-green-700 text-white'
                    : 'bg-neutral-700 text-neutral-400'
                }`}
              >
                {s}
              </span>
            ))}
          </div>

          {/* ── Step 1: Reason + checkbox ── */}
          {step === 1 && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded border border-red-800 bg-red-950/40 p-4">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-400" />
                <div className="text-sm text-red-200">
                  <p className="font-semibold">Aviso regulatório</p>
                  <p className="mt-1 whitespace-pre-wrap">{reason}</p>
                </div>
              </div>

              {preservedTables.length > 0 && (
                <div className="rounded border border-neutral-700 bg-neutral-800 p-3 text-xs text-neutral-300">
                  <p className="font-semibold text-neutral-200">
                    Tabelas preservadas (renomeadas, não excluídas):
                  </p>
                  <ul className="mt-2 list-inside list-disc space-y-0.5">
                    {preservedTables.map((t) => (
                      <li key={t} className="font-mono text-[#00FFA7]">
                        {t} → _orphan_{slug}_{t}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={checkboxChecked}
                  onChange={(e) => setCheckboxChecked(e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-[#00FFA7]"
                />
                <span className="text-sm text-neutral-200">{checkboxLabel}</span>
              </label>

              <div className="flex justify-end gap-2">
                <button
                  onClick={onClose}
                  className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-neutral-300 hover:bg-neutral-800"
                >
                  Cancelar
                </button>
                <button
                  disabled={!checkboxChecked}
                  onClick={() => setStep(2)}
                  className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Próximo →
                </button>
              </div>
            </div>
          )}

          {/* ── Step 2: ZIP password ── */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded border border-amber-700 bg-amber-950/30 p-4">
                <Lock className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
                <div className="text-sm text-amber-200">
                  <p className="font-semibold">Senha do export (AES-256)</p>
                  <p className="mt-1 text-amber-300">
                    O arquivo de export será criptografado com esta senha. Anote em local seguro —
                    sem ela, o arquivo é inutilizável.
                  </p>
                </div>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-400">
                    Senha (mín. 8 caracteres)
                  </label>
                  <input
                    type="password"
                    value={zipPassword}
                    onChange={(e) => setZipPassword(e.target.value)}
                    placeholder="Senha do ZIP de export"
                    className="w-full rounded border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:border-[#00FFA7] focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-400">
                    Confirmar senha
                  </label>
                  <input
                    type="password"
                    value={zipPasswordConfirm}
                    onChange={(e) => setZipPasswordConfirm(e.target.value)}
                    placeholder="Repita a senha"
                    className="w-full rounded border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:border-[#00FFA7] focus:outline-none"
                  />
                  {zipPassword && zipPasswordConfirm && !passwordsMatch && (
                    <p className="mt-1 text-xs text-red-400">
                      {zipPassword.length < 8
                        ? 'Senha deve ter pelo menos 8 caracteres.'
                        : 'Senhas não coincidem.'}
                    </p>
                  )}
                </div>
              </div>

              <div className="flex justify-between gap-2">
                <button
                  onClick={() => setStep(1)}
                  className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-neutral-300 hover:bg-neutral-800"
                >
                  ← Voltar
                </button>
                <button
                  disabled={!passwordsMatch}
                  onClick={() => setStep(3)}
                  className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Próximo →
                </button>
              </div>
            </div>
          )}

          {/* ── Step 3: Typed phrase confirmation ── */}
          {step === 3 && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded border border-neutral-700 bg-neutral-800 p-4">
                <Shield className="mt-0.5 h-5 w-5 shrink-0 text-neutral-400" />
                <div className="text-sm text-neutral-200">
                  <p>
                    Digite exatamente a frase abaixo para confirmar a desinstalação:
                  </p>
                  <p className="mt-2 rounded bg-neutral-900 px-3 py-1.5 font-mono text-[#00FFA7]">
                    {requiredPhrase}
                  </p>
                </div>
              </div>

              <input
                type="text"
                value={typedPhrase}
                onChange={(e) => setTypedPhrase(e.target.value)}
                placeholder={requiredPhrase}
                className="w-full rounded border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-white placeholder-neutral-500 focus:border-[#00FFA7] focus:outline-none"
              />
              {typedPhrase && !phraseMatches && (
                <p className="text-xs text-red-400">
                  Texto deve ser exatamente: <span className="font-mono">{requiredPhrase}</span>
                </p>
              )}

              {error && (
                <p className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
                  {error}
                </p>
              )}

              <div className="flex justify-between gap-2">
                <button
                  onClick={() => setStep(2)}
                  className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-neutral-300 hover:bg-neutral-800"
                >
                  ← Voltar
                </button>
                <button
                  disabled={!phraseMatches || uninstalling}
                  onClick={handleUninstall}
                  className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {uninstalling ? (
                    <>Desinstalando…</>
                  ) : (
                    <>
                      <Trash2 className="h-4 w-4" />
                      Desinstalar definitivamente
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
