from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_FILES = (
    "docker-compose.hub.yml",
    "docker-compose.proxy.yml",
    "evonexus.stack.yml",
)


def _load_compose(path: Path) -> dict:
    rendered = path.read_text().replace("${EVONEXUS_IMAGE_TAG:?set EVONEXUS_IMAGE_TAG to an immutable sha-* tag}", "sha-test")
    return yaml.safe_load(rendered)


def test_production_deployments_require_one_immutable_revision_tag():
    for relative_path in DEPLOYMENT_FILES:
        contents = (ROOT / relative_path).read_text()
        assert ":latest" not in contents
        assert "${EVONEXUS_IMAGE_TAG:?set EVONEXUS_IMAGE_TAG to an immutable sha-* tag}" in contents

        services = _load_compose(ROOT / relative_path)["services"]
        dashboard = services.get("dashboard", services.get("evonexus_dashboard"))
        assert dashboard["image"].endswith(":sha-test")


def test_scheduler_uses_runtime_image_at_the_same_revision_as_dashboard():
    for relative_path in DEPLOYMENT_FILES:
        services = _load_compose(ROOT / relative_path)["services"]
        scheduler = services.get("scheduler", services.get("evonexus_scheduler"))
        assert "evo-nexus-runtime" in scheduler["image"]
        assert "sha-test" in scheduler["image"]


def test_runtime_image_installs_claude_and_hermes():
    dockerfile = (ROOT / "Dockerfile.swarm").read_text()
    assert "@anthropic-ai/claude-code" in dockerfile
    assert "uv tool install hermes-agent" in dockerfile


def test_stack_checks_dashboard_and_scheduler_capabilities():
    stack = (ROOT / "evonexus.stack.yml").read_text()
    assert 'evonexus_dashboard:' in stack
    assert 'command -v claude && command -v hermes && curl -fsS http://localhost:${EVONEXUS_PORT:-8080}/api/version' in stack
    assert 'evonexus_scheduler:' in stack
    assert 'test: ["CMD-SHELL", "command -v claude && command -v hermes"]' in stack


def test_publish_workflow_does_not_publish_latest():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text()
    assert "latest=" not in workflow
    assert "type=sha,prefix=sha-,format=short" in workflow


def test_optional_hermes_api_server_is_disabled_by_default_and_requires_a_key():
    script = (ROOT / "start-dashboard.sh").read_text()

    assert "${EVONEXUS_HERMES_API_ENABLED:-false}" in script
    assert '-z "${EVONEXUS_HERMES_API_KEY:-}"' in script
    assert 'HERMES_API_PORT="${HERMES_API_PORT:-8642}"' in script


def test_dashboard_images_build_frontend_from_the_locked_workspace():
    for name in ("Dockerfile.dashboard", "Dockerfile.dev", "Dockerfile.swarm.dashboard"):
        dockerfile = (ROOT / name).read_text()
        assert "WORKDIR /dashboard" in dockerfile
        assert "COPY dashboard/package.json dashboard/package-lock.json ./" in dockerfile
        assert "COPY dashboard/packages/ui/package.json packages/ui/package.json" in dockerfile
        assert dockerfile.index("packages/ui/package.json") < dockerfile.index("RUN npm ci")
        assert "RUN npm run build --workspace frontend" in dockerfile
        assert "COPY --from=frontend-build /dashboard/frontend/dist" in dockerfile

    for name in ("Dockerfile.dev", "Dockerfile.swarm.dashboard"):
        dockerfile = (ROOT / name).read_text()
        assert (
            "COPY dashboard/terminal-server/package.json "
            "dashboard/terminal-server/package-lock.json ./"
        ) in dockerfile
        assert "RUN npm ci --omit=dev --no-audit --no-fund" in dockerfile


def test_dashboard_workspace_lock_includes_linux_tailwind_bindings():
    lockfile = (ROOT / "dashboard/package-lock.json").read_text()
    for package in (
        "frontend/node_modules/@tailwindcss/oxide-linux-arm64-musl",
        "frontend/node_modules/@tailwindcss/oxide-linux-x64-musl",
    ):
        assert package in lockfile


def test_dashboard_deployments_persist_tailscale_identity():
    services = {
        "docker-compose.hub.yml": "dashboard",
        "docker-compose.proxy.yml": "dashboard",
        "evonexus.stack.yml": "evonexus_dashboard",
    }
    for filename, service_name in services.items():
        deployment = _load_compose(ROOT / filename)
        assert "evonexus_tailscale_state" in deployment["volumes"]
        mounts = deployment["services"][service_name]["volumes"]
        assert "evonexus_tailscale_state:/var/lib/tailscale" in mounts


def test_dashboard_images_apply_database_migrations_before_startup():
    dashboard_dockerfile = (ROOT / "Dockerfile.dashboard").read_text()
    assert "COPY dashboard/alembic/ dashboard/alembic/" in dashboard_dockerfile
    assert "python -m alembic upgrade head" in dashboard_dockerfile

    supervisor = (ROOT / "start-dashboard.sh").read_text()
    migration = "cd /workspace/dashboard/alembic && uv run python -m alembic upgrade head"
    assert migration in supervisor
    assert supervisor.index(migration) < supervisor.index(
        "uv run python /workspace/dashboard/backend/app.py"
    )


def test_ci_covers_hermes_runtime_security_and_deployment_checks():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for required in (
        "tests/backend",
        "tests/db",
        "tests/heartbeats",
        "tests/test_deployment_images.py",
        "ADWs/test_hermes_integration.py",
        "ADWs/test_runtime_policy.py",
        "docker compose -f docker-compose.hub.yml config",
    ):
        assert required in workflow
