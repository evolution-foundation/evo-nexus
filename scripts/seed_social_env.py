#!/usr/bin/env python3
"""Seed config/social.env from the root .env social credentials.

config/social.env is the persistent social token store (see post_to_x.py):
on the VPS it lives in the evonexus_config volume, so refreshed tokens
survive redeploys. Run this after (re)authenticating via social-auth to
export the fresh tokens, then copy the file to the VPS at
/workspace/config/social.env.

Usage:
    python3 scripts/seed_social_env.py            # write config/social.env
    python3 scripts/seed_social_env.py --print    # print to stdout (to paste on the VPS)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SOCIAL_ENV_PATH = ROOT / "config" / "social.env"

EXTRA_KEYS = {"TWITTER_CLIENT_ID", "TWITTER_CLIENT_SECRET"}

HEADER = (
    "# Token store persistente das redes sociais (gitignored).\n"
    "# X rotaciona o refresh_token a cada refresh — post_to_x.py grava aqui.\n"
    "# Na VPS este arquivo vive em /workspace/config (volume evonexus_config).\n"
)


def main() -> int:
    if not ENV_PATH.exists():
        raise SystemExit(f".env não encontrado em {ENV_PATH}")
    keep = []
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.startswith("SOCIAL_") or key in EXTRA_KEYS:
            keep.append(stripped)
    if not keep:
        raise SystemExit("Nenhuma chave SOCIAL_* encontrada no .env")
    content = HEADER + "\n".join(keep) + "\n"
    if "--print" in sys.argv:
        print(content, end="")
        return 0
    SOCIAL_ENV_PATH.write_text(content, encoding="utf-8")
    SOCIAL_ENV_PATH.chmod(0o600)
    print(f"{SOCIAL_ENV_PATH} gravado com {len(keep)} chaves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
