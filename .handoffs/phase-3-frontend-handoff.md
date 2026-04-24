# Phase 3 — Frontend Handoff: Onboarding Wizard, Oracle Banner, Brain Repo Settings

## What was done

### New files created

#### Onboarding wizard (`dashboard/frontend/src/pages/onboarding/`)

| File | Purpose |
|------|---------|
| `OnboardingRouter.tsx` | Root component — manages flow/step state, calls `GET /api/onboarding/state` on mount, orchestrates rendering |
| `Welcome.tsx` | Step 0 — NetworkCanvas background, two buttons: "Configure from scratch" / "Restore brain repo" |
| `StepProvider.tsx` | Step 1 — Card grid for Anthropic/OpenAI/OpenRouter/Codex, password input, calls `POST /api/onboarding/provider` |
| `StepBrainRepo.tsx` | Step 2 — Explanation of brain repo + Yes/No CTA |
| `StepBrainConnect.tsx` | Step 2a — PAT input with toggle, "Create PAT" link to GitHub, calls `POST /api/brain-repo/connect` |
| `StepBrainChoose.tsx` | Step 2b — Create new repo or pick existing from `GET /api/brain-repo/detect`, calls `POST /api/brain-repo/connect` |
| `StepConfirm.tsx` | Step 3 — Summary card, "Finish setup" → `POST /api/onboarding/complete`, "Skip" → `POST /api/onboarding/skip` |

#### Restore flow (`dashboard/frontend/src/pages/onboarding/restore/`)

| File | Purpose |
|------|---------|
| `RestoreFlow.tsx` | State machine: select-repo → select-snapshot → confirm → execute |
| `RestoreSelectRepo.tsx` | PAT input + auto-detect repos via `GET /api/brain-repo/detect` |
| `RestoreSelectSnapshot.tsx` | 4 sections (HEAD, milestones, weekly, daily) via `GET /api/brain-repo/snapshots`, checkbox for include_kb |
| `RestoreConfirm.tsx` | Workspace name confirmation input (text must match username) |
| `RestoreExecute.tsx` | SSE consumer for `POST /api/brain-repo/restore/start`, progress bar, step list, retry button |

#### Other new files

| File | Purpose |
|------|---------|
| `dashboard/frontend/src/components/OracleWelcomeBanner.tsx` | Horizontal banner (not a modal), renders only when `user.onboarding_completed_agents_visit === false`, dismisses via `POST /api/auth/mark-agents-visited` + `refreshUser()` |
| `dashboard/frontend/src/pages/settings/BrainRepo.tsx` | Full settings page: status card, sync now, create milestone, disconnect with confirmation |

### Modified files

#### `App.tsx`
- Added `lazy` imports for `OnboardingRouter` and `BrainRepo`
- Added `OnboardingUser` interface for onboarding-specific user fields
- Added onboarding guard: if `user.onboarding_state` is not `completed`/`skipped`/null → redirect to `/onboarding`
- Added routes: `/onboarding/*` and `/settings/brain-repo`
- All wrapped in `<Suspense>` with consistent fallback

#### `Agents.tsx`
- Imported `OracleWelcomeBanner`
- Added `<OracleWelcomeBanner />` as first child of the returned `<div>` (above the header)

#### `Integrations.tsx`
- Added `brainRepoStatus` state with `useEffect` fetching `GET /api/brain-repo/status`
- Added "GitHub (Brain Repo)" card section in the integrations tab, before Core Integrations
- Card shows: connected/disconnected state, repo URL (linked), last sync date, pending count badge, Manage/Connect button

## Design decisions

- All onboarding components use the exact design tokens from `Login.tsx` (colors, border classes, shadow)
- `OracleWelcomeBanner` is a non-blocking horizontal strip — it does not use modal/overlay
- Brain repo calls in `Integrations.tsx` use `.catch(() => {})` so the page degrades gracefully if the feature is not deployed
- TypeScript: `brainRepoStatus` uses `any` annotation on the API call to avoid requiring the backend type to be defined in the frontend
- The onboarding guard in `App.tsx` is additive — it does not interfere with the `needsSetup` guard

## Build status

`npm run build` — zero TypeScript errors, built successfully in ~1.1s.

## What the next phase needs to do

- Implement the backend endpoints listed in the API spec (routes in `dashboard/backend/routes/onboarding.py` and `brain_repo/` modules already started)
- Add `onboarding_state` and `onboarding_completed_agents_visit` fields to the `/auth/me` response so the frontend onboarding guard activates
- Implement `GET /api/brain-repo/detect` to filter repos with `.evo-brain` metadata
- Implement `POST /api/brain-repo/restore/start` as an SSE stream
