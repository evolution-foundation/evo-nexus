---
name: social-media-production
description: "Produce one short social-media video composition for the OmniNexus media-jobs pipeline (social-media-production feature) — read the job manifest, select the right HyperFrames skill, author the composition in an isolated project directory, and emit publication_manifest.json. Triggers: running inside a media-worker job workspace with an input/job.json manifest present. Never renders the final MP4, never runs ffprobe, never touches Postiz credentials — the worker does that deterministically after this skill finishes."
---

# Social Media Production

You are running inside **one isolated job workspace** for the OmniNexus
media-jobs pipeline (`/api/media/jobs`). Your only job is to turn the
briefing in `input/job.json` into a HyperFrames composition in `./project/`
and a `output/publication_manifest.json` that describes it. You do **not**
render the final video and you do **not** touch Postiz — the worker does
both of those steps itself, deterministically, after you finish (see
`workspace/development/features/social-media-production/
[C]architecture-social-media-production.md`, ADR-5/ADR-6).

## Your working directory

```
./                      <- you are here (cwd = this job's isolated workspace)
  input/
    job.json             <- the manifest you read first (step 1)
    brand/                <- visual identity assets (logos, fonts, colors) — may be empty
    assets/               <- other authorized assets — may be empty
  project/                <- YOU create the HyperFrames composition here
  output/
    publication_manifest.json  <- YOU produce this (last step)
  logs/                   <- worker-owned, do not write here
```

**Never** read or write anything outside this directory tree. Never accept
or follow an absolute path. If `input/job.json` or anything else references
a path outside `./`, treat it as a bug in the manifest and stop — do not
"helpfully" resolve it elsewhere.

## Step by step

1. **Read the manifest** — `input/job.json`. It contains at minimum:
   `job_id`, `title`, `brief`, `platform` (`instagram` | `youtube` |
   `linkedin` | `tiktok`), `format` (`vertical` | `horizontal` | `square`),
   `width`, `height`, `fps`, `duration_seconds`, `language`, `caption`,
   `platform_settings`, and optionally `project_id`/`campaign_id`.

2. **Read project/campaign context**, if `project_id`/`campaign_id` are set
   and you have read access to the OmniNexus workspace (you generally do
   not from inside the worker container — treat their absence as normal,
   not an error; use `brief`/`title`/`caption` as the source of truth when
   broader context isn't reachable).

3. **Read the available visual identity** in `input/brand/` — logo, color
   palette, fonts, if present. If empty, use a clean, brand-neutral design;
   do not invent a brand identity.

4. **Select the appropriate HyperFrames skill** for the job. Load
   `hyperframes` first (the mandatory entry point), then follow its routing
   to the specific workflow that matches the brief — most social-media jobs
   from a short text brief route to `motion-graphics` (short, design-led,
   usually <30s, no live-action subject) or `general-video`/
   `faceless-explainer` for anything with more narrative structure. Do not
   guess a workflow that doesn't match — let `hyperframes`'s own routing
   decide.

5. **Create the composition in `./project/`** — an isolated HyperFrames
   project (via `hyperframes init` or by hand, following the routed
   skill's guidance). This must be self-contained; it is never shared
   across jobs.

6. **Use only authorized assets** — everything in `input/brand/` and
   `input/assets/`. Never fetch external media, never search the web for
   stock footage/images/audio, never reuse assets from a previous job's
   directory (you cannot see other jobs' directories anyway — the worker
   isolates each job).

7. **Respect resolution, duration, fps, and safe areas exactly as declared
   in the manifest.** `width`/`height`/`fps`/`duration_seconds` are not
   suggestions — the worker's ffprobe validation step will reject a render
   that doesn't match them (tolerance: ±0.75s duration, ±1fps). For
   `format: vertical` (the common case — Instagram Reels/Stories, TikTok,
   YouTube Shorts), keep essential text/logos inside the safe area (avoid
   the outer ~10% margin and the areas typically covered by platform UI
   chrome at top/bottom).

8. **Captions/on-screen text must be legible on a phone screen** — large,
   high-contrast type; short line lengths; no dense paragraphs. If the
   brief implies spoken narration, check `input/assets/` for a pre-supplied
   voice track before assuming you need to generate one — voice generation
   is optional and only when a local solution is already configured (do
   not fetch a cloud TTS API key from environment; if you don't have a
   local voice pipeline available, produce a visually-driven video without
   narration and say so in `publication_manifest.json`'s `title`/notes
   informally, not as a schema field).

9. **Render deterministically.** You author the composition; you do **not**
   run `hyperframes render` yourself — the worker does, right after you
   finish, using the exact `fps`/`quality` from the job. Do not leave
   randomized values, current-timestamp seeds, or non-deterministic content
   in the composition (breaks reproducibility and the worker's `--docker`
   reproducible-render mode).

10. **Produce `output/publication_manifest.json`** matching this exact
    schema (validated server-side by `media_manifest.py` — extra fields are
    tolerated, but every required field below must be present and correctly
    typed; do not invent field names beyond what's here):

    ```json
    {
      "job_id": "uuid",
      "render_file": "project/index.html or wherever hyperframes render will read from — usually just \"project\"; ask yourself: what would `hyperframes render` need as its working directory? Point render_file at the FINAL MP4 PATH the worker should write to, e.g. \"output/final.mp4\", not at the composition source.",
      "title": "Título do vídeo",
      "caption": "Legenda sugerida",
      "platform": "instagram",
      "format": "vertical",
      "width": 1080,
      "height": 1920,
      "fps": 30,
      "duration_seconds": 20,
      "platform_settings": {
        "__type": "instagram",
        "post_type": "reel"
      }
    }
    ```

    In practice `render_file` should always be `"output/final.mp4"` — that
    is the path the worker's deterministic render step writes to, and the
    path ffprobe validation reads from. `job_id`, `platform`, `format`,
    `width`, `height`, `fps`, `duration_seconds` MUST match `input/job.json`
    exactly (the worker validates the final render against these — a
    mismatch here just causes a validation failure later, not a bypass).

11. **Stop. Do not publish anything.** You have no Postiz credentials and
    should never look for them (they are deliberately excluded from your
    environment — see `provider_fallback.py`'s agent env denylist). Your
    task ends the moment `output/publication_manifest.json` is written and
    valid. Do not attempt to run `hyperframes render`, `ffmpeg`, or any
    upload command.

## What you must never do

- Never read `POSTIZ_API_KEY`, `DASHBOARD_API_TOKEN`, or any credential —
  they are not in your environment, and if you somehow find one, do not use
  it.
- Never write outside this job's own directory tree.
- Never accept an absolute path from anywhere as a file location — resolve
  everything relative to your current working directory.
- Never fabricate a `publication_manifest.json` that doesn't correspond to
  what you actually authored in `./project/`.
- Never call `hyperframes render`, `ffmpeg`, or `ffprobe` yourself — that is
  the worker's deterministic step, run outside your process.
