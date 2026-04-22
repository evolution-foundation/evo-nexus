import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import i18n from './i18n'
import App from './App'

// Guarantee that i18n resources are registered before the first React
// render. Without this, the first pass through <Setup/>, <Login/>, etc
// runs with `ready=false` from `useTranslation()` and `t('setup.appSubtitle')`
// falls back to returning the raw key. Users on fresh installs saw the UI
// render briefly as literal keys ("setup.appSubtitle", "setup.tabWorkspace")
// before the second render corrected it — and under some React Router
// transitions the corrected render never arrived.
//
// The resource bundles are bundled inline (no network), so this Promise
// resolves within one micro-task in practice — the visible startup delay
// is zero.
function waitForI18n(): Promise<void> {
  if (i18n.isInitialized) return Promise.resolve()
  return new Promise((resolve) => {
    i18n.on('initialized', () => resolve())
  })
}

waitForI18n().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  )
})
