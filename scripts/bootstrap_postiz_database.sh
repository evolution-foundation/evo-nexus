#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${POSTGRES_SERVICE_NAME:-postgres_postgres}"
ADMIN_USER="${POSTGRES_ADMIN_USER:-postgres}"
DB_NAME="postiz_db_local"
DB_USER="postiz_user"

if [[ -z "${POSTIZ_DB_PASSWORD:-}" ]]; then
  echo "Set POSTIZ_DB_PASSWORD first. Use a URL-safe value, e.g.:" >&2
  echo "  export POSTIZ_DB_PASSWORD=\$(openssl rand -hex 24)" >&2
  exit 1
fi

if [[ -z "${POSTGRES_ADMIN_PASSWORD:-}" ]]; then
  echo "Set POSTGRES_ADMIN_PASSWORD to the password of the existing postgres role." >&2
  exit 1
fi

container_id="$(docker ps \
  --filter "label=com.docker.swarm.service.name=${SERVICE_NAME}" \
  --format '{{.ID}}' | head -n 1)"

if [[ -z "$container_id" ]]; then
  echo "No running container found for Swarm service ${SERVICE_NAME}." >&2
  exit 1
fi

docker exec -i -e PGPASSWORD="$POSTGRES_ADMIN_PASSWORD" "$container_id" \
  psql --username "$ADMIN_USER" --dbname postgres \
  --set ON_ERROR_STOP=1 --set db_password="$POSTIZ_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE postiz_user LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postiz_user') \gexec

SELECT 'CREATE DATABASE postiz_db_local OWNER postiz_user'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'postiz_db_local') \gexec

ALTER ROLE postiz_user WITH LOGIN PASSWORD :'db_password';
GRANT ALL PRIVILEGES ON DATABASE postiz_db_local TO postiz_user;
SQL

echo "Postiz database is ready in ${SERVICE_NAME}: ${DB_NAME} (owner ${DB_USER})."
