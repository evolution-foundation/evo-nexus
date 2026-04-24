"""Regression tests — only github:/https: sources are accepted.

Local filesystem paths, file://, ssh://, and other schemes must be rejected
by resolve_source and resolve_source_with_sha with a ValueError. This closes
the doc/code mismatch from the Plugins v1 review — the SKILL.md promised
rejection, the code accepted anything.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from plugin_loader import PluginInstaller  # noqa: E402


REJECTED_SOURCES = [
    "/etc",
    "/etc/passwd",
    "/root/.ssh/id_rsa",
    "/Users/someone/projects/plugin",
    "./relative/path",
    "../escape",
    "~/plugin",
    "file:///etc/passwd",
    "ssh://git@github.com/owner/repo",
    "git@github.com:owner/repo.git",
    "",
    "plugin.yaml",
]


@pytest.mark.parametrize("source", REJECTED_SOURCES)
def test_resolve_source_rejects_non_http(source):
    with pytest.raises(ValueError):
        PluginInstaller.resolve_source(source)


@pytest.mark.parametrize("source", REJECTED_SOURCES)
def test_resolve_source_with_sha_rejects_non_http(source):
    with pytest.raises(ValueError):
        PluginInstaller().resolve_source_with_sha(source)


def test_resolve_source_rejects_github_without_slash():
    with pytest.raises(ValueError):
        PluginInstaller.resolve_source("github:owneronly")
