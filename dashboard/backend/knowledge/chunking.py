"""Structural-first markdown chunker with token-based fallback.

Algorithm:
  1. If markdown has headings (H1-H3): split by heading.
     Each section becomes 1 chunk if ≤ MAX_TOKENS tokens;
     if > MAX_TOKENS, sub-split by paragraphs grouped to ~TARGET_TOKENS with
     1-paragraph overlap.
  2. If no headings (raw text / transcript): token-based split,
     TARGET_TOKENS tokens per chunk, OVERLAP_TOKENS token overlap.

Chunk type inference:
    "heading"   — section starts with a heading
    "code"      — content contains ``` fence
    "table"     — content contains Markdown table (|)
    "list"      — content has bullet/numbered list (- / * / 1.)
    "paragraph" — default

TODO (v1.1): KNOWLEDGE_TOPIC_SEGMENTATION — LLM-based topic boundary detection.
    Activate only when env var is set AND token count > 5000.
    Not implemented — deferred to v1.1.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, TypedDict


# ---------------------------------------------------------------------------
# Config (env-tunable)
# ---------------------------------------------------------------------------

MAX_TOKENS = int(os.environ.get("KNOWLEDGE_CHUNK_MAX_TOKENS", "1000"))
TARGET_TOKENS = int(os.environ.get("KNOWLEDGE_CHUNK_TARGET_TOKENS", "500"))
OVERLAP_TOKENS = int(os.environ.get("KNOWLEDGE_CHUNK_OVERLAP_TOKENS", "10"))

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------

class Chunk(TypedDict):
    content: str
    chunk_idx: int
    chunk_type: str
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------------
# Tokenizer (tiktoken, lazy)
# ---------------------------------------------------------------------------

_enc: Optional[Any] = None


def _get_encoder() -> Any:
    global _enc
    if _enc is not None:
        return _enc
    try:
        import tiktoken  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "tiktoken is not installed. "
            "Run: pip install tiktoken  (or: uv add tiktoken)"
        ) from exc
    _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def _count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text))


# ---------------------------------------------------------------------------
# Chunk type inference
# ---------------------------------------------------------------------------

def _infer_chunk_type(text: str) -> str:
    """Infer chunk type from content patterns."""
    stripped = text.strip()
    if re.search(r"```", stripped):
        return "code"
    if re.search(r"^\|.+\|", stripped, re.MULTILINE):
        return "table"
    if re.search(r"^(\s*[-*]\s+|\s*\d+\.\s+)", stripped, re.MULTILINE):
        return "list"
    if stripped.startswith("#"):
        return "heading"
    return "paragraph"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_markdown(md: str, doc_metadata: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Split *md* into structured chunks.

    Args:
        md: full document markdown text
        doc_metadata: optional document metadata (passed through to each chunk)

    Returns:
        List of Chunk dicts, ordered by chunk_idx (0-based).
    """
    if not md or not md.strip():
        return []

    if doc_metadata is None:
        doc_metadata = {}

    headings = list(_HEADING_RE.finditer(md))

    if headings:
        return _chunk_by_headings(md, headings, doc_metadata)
    else:
        return _chunk_by_tokens(md, doc_metadata)


# ---------------------------------------------------------------------------
# Structural split (headings)
# ---------------------------------------------------------------------------

def _chunk_by_headings(
    md: str,
    headings: List[re.Match],  # type: ignore[type-arg]
    doc_metadata: Dict[str, Any],
) -> List[Chunk]:
    """Split markdown by H1-H3 headings."""
    chunks: List[Chunk] = []
    idx = 0

    # Build heading path tracker for nested headings
    heading_path: Dict[int, str] = {}  # level → heading text

    sections: List[Dict[str, Any]] = []
    for i, match in enumerate(headings):
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(md)
        section_content = md[start:end].strip()
        sections.append({
            "level": level,
            "heading": heading_text,
            "content": section_content,
        })

    # Pre-heading content (before first heading)
    if headings[0].start() > 0:
        pre = md[:headings[0].start()].strip()
        if pre:
            sections.insert(0, {"level": 0, "heading": None, "content": pre})

    for section in sections:
        heading_text = section["heading"]
        level = section["level"]
        content = section["content"]

        # Update heading path
        if level > 0:
            heading_path[level] = heading_text  # type: ignore[assignment]
            # Clear deeper levels
            for deeper_level in list(heading_path.keys()):
                if deeper_level > level:
                    del heading_path[deeper_level]

        current_heading_path = " > ".join(
            v for k, v in sorted(heading_path.items()) if v
        )

        token_count = _count_tokens(content)
        if token_count <= MAX_TOKENS:
            chunks.append(_make_chunk(
                content=content,
                chunk_idx=idx,
                doc_metadata=doc_metadata,
                section=_find_page(content, doc_metadata),
                heading_path=current_heading_path,
            ))
            idx += 1
        else:
            # Sub-split by paragraphs
            sub_chunks = _split_large_section(
                content=content,
                start_idx=idx,
                doc_metadata=doc_metadata,
                heading_path=current_heading_path,
            )
            chunks.extend(sub_chunks)
            idx += len(sub_chunks)

    return chunks


def _split_large_section(
    content: str,
    start_idx: int,
    doc_metadata: Dict[str, Any],
    heading_path: str,
) -> List[Chunk]:
    """Sub-split a large section by paragraphs, grouping to ~TARGET_TOKENS with 1-para overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", content) if p.strip()]
    if not paragraphs:
        return []

    chunks: List[Chunk] = []
    current_group: List[str] = []
    current_tokens = 0
    idx = start_idx
    last_para: Optional[str] = None

    for para in paragraphs:
        para_tokens = _count_tokens(para)

        if current_tokens + para_tokens > TARGET_TOKENS and current_group:
            # Emit current group
            chunk_text = "\n\n".join(current_group)
            chunks.append(_make_chunk(
                content=chunk_text,
                chunk_idx=idx,
                doc_metadata=doc_metadata,
                section=_find_page(chunk_text, doc_metadata),
                heading_path=heading_path,
            ))
            idx += 1
            # Start new group with overlap (last paragraph of previous group)
            current_group = [last_para, para] if last_para else [para]
            current_tokens = _count_tokens("\n\n".join(current_group))
        else:
            current_group.append(para)
            current_tokens += para_tokens

        last_para = para

    # Flush remaining
    if current_group:
        chunk_text = "\n\n".join(current_group)
        chunks.append(_make_chunk(
            content=chunk_text,
            chunk_idx=idx,
            doc_metadata=doc_metadata,
            section=_find_page(chunk_text, doc_metadata),
            heading_path=heading_path,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Token-based fallback (no headings)
# ---------------------------------------------------------------------------

def _chunk_by_tokens(md: str, doc_metadata: Dict[str, Any]) -> List[Chunk]:
    """Token-based split: TARGET_TOKENS per chunk, OVERLAP_TOKENS overlap."""
    enc = _get_encoder()
    tokens = enc.encode(md)

    if not tokens:
        return []

    chunks: List[Chunk] = []
    step = TARGET_TOKENS - OVERLAP_TOKENS
    if step <= 0:
        step = TARGET_TOKENS

    idx = 0
    pos = 0
    while pos < len(tokens):
        window = tokens[pos: pos + TARGET_TOKENS]
        text = enc.decode(window)
        chunks.append(_make_chunk(
            content=text,
            chunk_idx=idx,
            doc_metadata=doc_metadata,
            section=None,
            heading_path=None,
        ))
        idx += 1
        pos += step

    return chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(
    content: str,
    chunk_idx: int,
    doc_metadata: Dict[str, Any],
    section: Optional[int],
    heading_path: Optional[str],
) -> Chunk:
    return Chunk(
        content=content,
        chunk_idx=chunk_idx,
        chunk_type=_infer_chunk_type(content),
        metadata={
            "page": section,
            "section": heading_path,
            "heading_path": heading_path,
        },
    )


def _find_page(content: str, doc_metadata: Dict[str, Any]) -> Optional[int]:
    """Best-effort page number inference (not tracked per-chunk in v1)."""
    return None
