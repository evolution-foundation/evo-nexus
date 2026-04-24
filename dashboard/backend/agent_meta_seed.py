"""Static seed of native agent metadata for the /api/agent-meta endpoint.

Mirrors dashboard/frontend/src/lib/agent-meta.ts (the 38 native agents).
Only label and avatar_url are needed server-side — the frontend retains its
own icon/color/command seed for pre-hydration rendering.

Wave 2.0: this seed is merged with plugin agent entries at request time.
Plugin agents contribute avatar_url = /plugins/{slug}/ui/{path}.
"""
from __future__ import annotations

from typing import Dict, Optional

# Each entry: {label: str, avatar_url: str | None}
# avatar_url follows the same pattern as /avatar/avatar_{slug}.webp on the frontend.
NATIVE_AGENT_SEED: Dict[str, Dict[str, Optional[str]]] = {
    "atlas-project": {
        "label": "Projects",
        "avatar_url": "/avatar/avatar_atlas.webp",
    },
    "clawdia-assistant": {
        "label": "Operations",
        "avatar_url": "/avatar/avatar_clawdia.webp",
    },
    "flux-finance": {
        "label": "Finance",
        "avatar_url": "/avatar/avatar_flux.webp",
    },
    "kai-personal-assistant": {
        "label": "Personal",
        "avatar_url": "/avatar/avatar_kai.webp",
    },
    "mentor-courses": {
        "label": "Courses",
        "avatar_url": "/avatar/avatar_mentor.webp",
    },
    "lumen-learning": {
        "label": "Learning Retention",
        "avatar_url": "/avatar/avatar_lumen.webp",
    },
    "nex-sales": {
        "label": "Sales",
        "avatar_url": "/avatar/avatar_nex.webp",
    },
    "pixel-social-media": {
        "label": "Social Media",
        "avatar_url": "/avatar/avatar_pixel.webp",
    },
    "pulse-community": {
        "label": "Community",
        "avatar_url": "/avatar/avatar_pulse.webp",
    },
    "sage-strategy": {
        "label": "Strategy",
        "avatar_url": "/avatar/avatar_sage.webp",
    },
    "oracle": {
        "label": "Knowledge",
        "avatar_url": "/avatar/avatar_oracle.webp",
    },
    "mako-marketing": {
        "label": "Marketing",
        "avatar_url": "/avatar/avatar_mako.webp",
    },
    "aria-hr": {
        "label": "HR / People",
        "avatar_url": "/avatar/avatar_aria.webp",
    },
    "zara-cs": {
        "label": "Customer Success",
        "avatar_url": "/avatar/avatar_zara.webp",
    },
    "lex-legal": {
        "label": "Legal",
        "avatar_url": "/avatar/avatar_lex.webp",
    },
    "nova-product": {
        "label": "Product",
        "avatar_url": "/avatar/avatar_nova.webp",
    },
    "dex-data": {
        "label": "Data / BI",
        "avatar_url": "/avatar/avatar_dex.webp",
    },
    "helm-conductor": {
        "label": "Cycle Orchestration",
        "avatar_url": "/avatar/avatar_helm.webp",
    },
    "mirror-retro": {
        "label": "Retrospective",
        "avatar_url": "/avatar/avatar_mirror.webp",
    },
    "apex-architect": {
        "label": "Architect",
        "avatar_url": "/avatar/avatar_apex.webp",
    },
    "bolt-executor": {
        "label": "Executor",
        "avatar_url": "/avatar/avatar_bolt.webp",
    },
    "canvas-designer": {
        "label": "Designer",
        "avatar_url": "/avatar/avatar_canvas.webp",
    },
    "compass-planner": {
        "label": "Planner",
        "avatar_url": "/avatar/avatar_compass.webp",
    },
    "echo-analyst": {
        "label": "Analyst",
        "avatar_url": "/avatar/avatar_echo.webp",
    },
    "flow-git": {
        "label": "Git Master",
        "avatar_url": "/avatar/avatar_flow.webp",
    },
    "grid-tester": {
        "label": "Test Engineer",
        "avatar_url": "/avatar/avatar_grid.webp",
    },
    "hawk-debugger": {
        "label": "Debugger",
        "avatar_url": "/avatar/avatar_hawk.webp",
    },
    "lens-reviewer": {
        "label": "Code Reviewer",
        "avatar_url": "/avatar/avatar_lens.webp",
    },
    "oath-verifier": {
        "label": "Verifier",
        "avatar_url": "/avatar/avatar_oath.webp",
    },
    "prism-scientist": {
        "label": "Scientist",
        "avatar_url": "/avatar/avatar_prism.webp",
    },
    "probe-qa": {
        "label": "QA Tester",
        "avatar_url": "/avatar/avatar_probe.webp",
    },
    "quill-writer": {
        "label": "Writer",
        "avatar_url": "/avatar/avatar_quill.webp",
    },
    "raven-critic": {
        "label": "Critic",
        "avatar_url": "/avatar/avatar_raven.webp",
    },
    "scout-explorer": {
        "label": "Explorer",
        "avatar_url": "/avatar/avatar_scout.webp",
    },
    "scroll-docs": {
        "label": "Document Specialist",
        "avatar_url": "/avatar/avatar_scroll.webp",
    },
    "trail-tracer": {
        "label": "Tracer",
        "avatar_url": "/avatar/avatar_trail.webp",
    },
    "vault-security": {
        "label": "Security Reviewer",
        "avatar_url": "/avatar/avatar_vault.webp",
    },
    "zen-simplifier": {
        "label": "Code Simplifier",
        "avatar_url": "/avatar/avatar_zen.webp",
    },
}
