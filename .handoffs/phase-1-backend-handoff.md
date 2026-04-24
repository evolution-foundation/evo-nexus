# Phase 1 Handoff — Brain Repo Backend Modules

## O que foi feito

Criados todos os módulos Python do pacote `dashboard/backend/brain_repo/` e as 5 rotinas ADW em `ADWs/routines/custom/`. Nenhum arquivo existente foi modificado.

## Arquivos criados

### Pacote `dashboard/backend/brain_repo/`

| Arquivo | Responsabilidade |
|---|---|
| `git_ops.py` | Wrapper subprocess para clone, commit, push, pull_rebase, tag, checkout_ref |
| `manifest.py` | Leitura/escrita de `manifest.yaml`, validação de schema, inicialização do repo |
| `migrations.py` | Registry de migrations de schema; noop para 1.0 |
| `secrets_scanner.py` | Scanner de 21 padrões de secrets; scan_files + scan_directory |
| `github_oauth.py` | PATAuthProvider + OAuthAuthProvider (esqueleto); Fernet encrypt/decrypt |
| `github_api.py` | REST GitHub API: detect_brain_repos, create_private_repo, list_snapshots, validate_pat_scopes, etc. |
| `watcher.py` | BrainRepoWatcher com debounce via watchdog; start_brain_watcher() |
| `sync_worker.py` | SyncWorker com fila de retry em disco; enqueue/process_pending/update_badge |
| `kb_mirror.py` | Export KB → markdown (Postgres opcional); import markdown → KB |
| `transcripts_mirror.py` | Cópia de .jsonl do Claude CLI com rolling window e pruning |
| `restore.py` | Motor de restore com streaming Generator[dict]; 10 passos com progress 0-100 |

### Rotinas `ADWs/routines/custom/`

| Arquivo | Trigger sugerido |
|---|---|
| `brain_sync_transcripts.py` | interval: 15 (minutos) |
| `brain_kb_mirror.py` | interval: 60 (minutos) |
| `brain_tag_daily.py` | time: "23:59" |
| `brain_tag_weekly.py` | time: "23:58" (domingo) |
| `brain_health.py` | interval: 5 (minutos) |

## Interfaces expostas (contratos para fases seguintes)

### `git_ops`
```python
clone(url, token, target: Path) -> None          # raises RuntimeError
commit_all(repo_dir, message) -> bool             # False = nothing to commit
push(repo_dir, token) -> bool                     # False = failed, never raises
pull_rebase(repo_dir, token) -> bool
create_tag(repo_dir, tag, message) -> bool
checkout_ref(repo_dir, ref, target_dir) -> None   # git archive | tar
_masked_url(url, token) -> str
```

### `manifest`
```python
MANIFEST_SCHEMA_VERSION = "1.0"
read_manifest(repo_dir) -> dict
write_manifest(repo_dir, data) -> None
validate_schema(manifest) -> tuple[bool, bool]   # (schema_ok, migration_needed)
initialize_brain_repo(repo_dir, config: dict) -> None
```

### `github_oauth`
```python
class PATAuthProvider(pat, master_key)
class OAuthAuthProvider(access_token=None, master_key=None)
class FeatureDisabledError(Exception)
decrypt_token(encrypted: bytes, master_key: bytes) -> str
is_oauth_enabled() -> bool
get_master_key() -> bytes
```

### `github_api`
```python
detect_brain_repos(token) -> list[dict]
list_user_repos(token) -> list[dict]
create_private_repo(token, name, description) -> dict  # raises RuntimeError on 4xx
validate_repo_is_private(token, owner, repo) -> bool
list_snapshots(token, owner, repo) -> dict  # {"daily": [], "weekly": [], "milestones": [], "head": {}}
validate_pat_scopes(token) -> tuple[bool, list[str]]
get_github_username(token) -> str
```

### `secrets_scanner`
```python
PATTERNS: list[tuple[str, str]]   # 21 (name, regex) pairs
scan_files(files: list[Path]) -> list[dict]
scan_directory(directory, exclude=None) -> list[dict]
# finding dict: {"file": str, "line": int, "pattern": str, "snippet": str}
```

### `watcher`
```python
class BrainRepoWatcher(install_dir, brain_repo_dir, sync_fn, debounce_seconds=30)
    .start() -> None
    .stop() -> None
start_brain_watcher(install_dir) -> BrainRepoWatcher | None
```

### `sync_worker`
```python
RETRY_INTERVAL_SECONDS = 60
PENDING_DIR_NAME = "brain-pending"          # em install_dir.parent/
class SyncWorker(install_dir, brain_repo_dir, token_fn)
    .enqueue(job_type="commit_push") -> None
    .process_pending() -> int
    .update_badge(user_id, count) -> None
    .start() -> None
    .stop() -> None
```

### `kb_mirror`
```python
export_kb_to_markdown(brain_repo_dir) -> int   # 0 se Postgres não configurado
import_markdown_to_kb(markdown_dir, master_key_matches) -> dict
# {"imported": N, "skipped": N, "errors": []}
```

### `transcripts_mirror`
```python
find_claude_projects_dir(service_user=None) -> Path | None
mirror_transcripts(install_dir, brain_repo_dir, days=30) -> int
```

### `restore`
```python
STAGING_DIR = Path("/tmp/brain-restore-staging")
execute_restore(repo_url, ref, token, install_dir, include_kb, kb_key_matches)
    -> Generator[dict, None, None]
# event: {"step": str, "progress": int, "message": str, "error": bool}
```

## Contratos importantes para fases seguintes

### Routes (phase 2)
- O restore usa `Generator` → deve ser servido via SSE com `stream_with_context` (Flask)
- `github_api.create_private_repo` levanta `RuntimeError` em 4xx — tratar no handler de rota
- `OAuthAuthProvider.handle_callback` levanta `FeatureDisabledError` se OAuth não configurado
- `secrets_scanner.scan_directory` NUNCA retorna token em snippets (mascarado: 4+***+4)

### Scheduler / routines.yaml
Sugestão de entradas para `routines.yaml`:
```yaml
- name: brain_sync_transcripts
  script: custom/brain_sync_transcripts
  interval: 15

- name: brain_kb_mirror
  script: custom/brain_kb_mirror
  interval: 60

- name: brain_tag_daily
  script: custom/brain_tag_daily
  time: "23:59"

- name: brain_tag_weekly
  script: custom/brain_tag_weekly
  time: "23:58"

- name: brain_health
  script: custom/brain_health
  interval: 5
```

### KB Mirror — Postgres
`kb_mirror.export_kb_to_markdown` retorna 0 silenciosamente se `POSTGRES_HOST` não estiver configurado. As tabelas esperadas são `knowledge_connections` e `knowledge_chunks` com os campos listados no módulo.

### SyncWorker — pending dir
O diretório de fila fica em `install_dir.parent / "brain-pending"`. Cada job é um arquivo `<uuid>.json`. Jobs com falha persistem com `attempts` e `last_error` atualizados para diagnóstico.

### Watcher
`start_brain_watcher` requer Flask app context ativo. O debounce padrão é 30s — configurável via `BrainRepoWatcher(..., debounce_seconds=N)`.

## Parse check
Todos os 16 arquivos passaram em `ast.parse()` sem erros de sintaxe.
