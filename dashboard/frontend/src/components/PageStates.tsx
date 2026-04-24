import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export function FullPageLoader({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0C111D] px-6">
      <div className="flex items-center gap-3 rounded-full border border-[#21262d] bg-[#0f1520] px-4 py-3 text-sm text-[#D0D5DD] shadow-[0_12px_40px_rgba(0,0,0,0.35)]">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#00FFA7]/25 border-t-[#00FFA7]" />
        <span>{label}</span>
      </div>
    </div>
  )
}

export function SectionLoader({ label = 'Loading section...' }: { label?: string }) {
  return (
    <div className="flex min-h-[40vh] items-center justify-center px-6 py-12">
      <div className="flex items-center gap-3 rounded-2xl border border-[#21262d] bg-[#0f1520] px-4 py-3 text-sm text-[#D0D5DD] shadow-[0_12px_40px_rgba(0,0,0,0.25)]">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#00FFA7]/25 border-t-[#00FFA7]" />
        <span>{label}</span>
      </div>
    </div>
  )
}

interface SectionBoundaryProps {
  sectionName: string
  children: ReactNode
}

interface SectionBoundaryState {
  hasError: boolean
  error: Error | null
}

export class SectionBoundary extends Component<SectionBoundaryProps, SectionBoundaryState> {
  state: SectionBoundaryState = {
    hasError: false,
    error: null,
  }

  static getDerivedStateFromError(error: Error): SectionBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ui] ${this.props.sectionName} failed to render`, error, info)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-[40vh] items-center justify-center px-6 py-12">
          <div className="w-full max-w-2xl rounded-3xl border border-red-500/20 bg-[#0f1520] p-6 shadow-[0_20px_60px_rgba(0,0,0,0.45)]">
            <div className="flex items-center gap-3 text-red-300">
              <AlertTriangle size={18} />
              <div>
                <h2 className="text-base font-semibold text-[#F9FAFB]">
                  Unable to load {this.props.sectionName}
                </h2>
                <p className="text-sm text-[#D0D5DD]">
                  An unexpected error interrupted this section.
                </p>
              </div>
            </div>

            <div className="mt-4 rounded-2xl border border-[#21262d] bg-[#0C111D] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#667085]">
                Error details
              </p>
              <p className="mt-2 text-sm text-[#FCA5A5] break-words">
                {this.state.error?.message || 'Unknown render failure'}
              </p>
            </div>

            <div className="mt-5 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={this.handleRetry}
                className="inline-flex items-center gap-2 rounded-xl border border-[#344054] bg-[#161b22] px-4 py-2 text-sm font-medium text-[#e6edf3] transition-colors hover:border-[#00FFA7]/50 hover:text-white"
              >
                <RefreshCw size={14} />
                Try again
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="inline-flex items-center gap-2 rounded-xl border border-[#00FFA7]/30 bg-[#00FFA7]/10 px-4 py-2 text-sm font-medium text-[#00FFA7] transition-colors hover:bg-[#00FFA7]/15"
              >
                Reload app
              </button>
            </div>
          </div>
        </div>
      )
    }

    return <>{this.props.children}</>
  }
}
