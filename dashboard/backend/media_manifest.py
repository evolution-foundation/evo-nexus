"""Schema validation for the agent-produced publication_manifest.json
(briefing Etapa 5): "Valide esse manifesto com schema. Não confie em paths
arbitrários fornecidos pelo agente."

The manifest is the only channel through which the OpenCode/HyperFrames
process hands the render's declared properties back to the worker — every
field is treated as untrusted input until validated here, and `render_file`
in particular is resolved through media_workspace.resolve_within() before
ever being opened.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from media_workspace import PathSecurityError, resolve_within

PUBLICATION_MANIFEST_SCHEMA = {
    "type": "object",
    "required": [
        "job_id", "render_file", "title", "platform", "format",
        "width", "height", "fps", "duration_seconds",
    ],
    "additionalProperties": True,
    "properties": {
        "job_id": {"type": "string", "minLength": 1},
        "render_file": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1, "maxLength": 500},
        "caption": {"type": "string"},
        "platform": {"type": "string", "enum": ["instagram", "youtube", "linkedin", "tiktok"]},
        "format": {"type": "string", "enum": ["vertical", "horizontal", "square"]},
        "width": {"type": "integer", "minimum": 1, "maximum": 8192},
        "height": {"type": "integer", "minimum": 1, "maximum": 8192},
        "fps": {"type": "integer", "enum": [24, 30, 60]},
        "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
        "platform_settings": {
            "type": "object",
            "required": ["__type"],
            "properties": {"__type": {"type": "string", "minLength": 1}},
        },
    },
}


class ManifestValidationError(ValueError):
    pass


def validate_manifest_dict(data: dict) -> None:
    try:
        jsonschema.validate(instance=data, schema=PUBLICATION_MANIFEST_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ManifestValidationError(f"publication_manifest.json inválido: {exc.message}") from None


def load_and_validate_manifest(job_dir: Path, manifest_relpath: str = "output/publication_manifest.json") -> dict:
    """Load, schema-validate, and return the manifest — plus resolve+return
    the absolute, workspace-confined path of the declared render_file so
    callers never do their own ad-hoc path joining on agent-controlled input.
    """
    try:
        manifest_path = resolve_within(job_dir, manifest_relpath)
    except PathSecurityError as exc:
        raise ManifestValidationError(str(exc)) from None

    if not manifest_path.is_file():
        raise ManifestValidationError(f"Manifesto não encontrado: {manifest_path}")

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestValidationError(f"publication_manifest.json não é JSON válido: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestValidationError("publication_manifest.json deve ser um objeto JSON.")

    validate_manifest_dict(data)

    try:
        render_path = resolve_within(job_dir, data["render_file"])
    except PathSecurityError as exc:
        raise ManifestValidationError(f"render_file inválido: {exc}") from None

    data["_resolved_render_path"] = str(render_path)
    return data
