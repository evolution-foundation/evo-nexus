# Phase 2 Handoff — Backend Routes: Onboarding + Brain Repo

## Arquivos criados / modificados

| Arquivo | Ação |
|---|---|
| `dashboard/backend/routes/auth_routes.py` | Modificado — import + 2 endpoints |
| `dashboard/backend/routes/onboarding.py` | Criado |
| `dashboard/backend/routes/brain_repo.py` | Criado |

---

## Endpoints criados

### auth_routes.py

#### `GET /api/auth/needs-onboarding`
- **Auth:** Público (em `PUBLIC_PATHS`)
- **Comportamento:** Retorna `needs_onboarding: false` se setup não foi feito ou usuário não autenticado; caso contrário delega a `needs_onboarding(current_user)` do models.
- **Resposta:** `{"needs_onboarding": bool, "onboarding_state": str | null}`

#### `POST /api/auth/mark-agents-visited`
- **Auth:** `@login_required`
- **Comportamento:** Seta `onboarding_completed_agents_visit = True` no usuário atual.
- **Resposta:** `{"ok": true}`

---

### onboarding.py (Blueprint `onboarding`)

#### `GET /api/onboarding/state`
- **Auth:** `@login_required`
- **Resposta:**
  ```json
  {
    "onboarding_state": "pending" | "completed" | "skipped" | null,
    "onboarding_completed_agents_visit": bool,
    "brain_repo_configured": bool,
    "brain_repo": {BrainRepoConfig.to_dict()} | null
  }
  ```

#### `POST /api/onboarding/start`
- Seta `onboarding_state = "pending"`. Resposta: `{"onboarding_state": "pending"}`

#### `POST /api/onboarding/complete`
- Seta `onboarding_state = "completed"`. Resposta: `{"onboarding_state": "completed"}`

#### `POST /api/onboarding/skip`
- Seta `onboarding_state = "skipped"`. Resposta: `{"onboarding_state": "skipped"}`

#### `POST /api/onboarding/provider`
- **Body:** `{"provider": str, "api_key": str (optional)}`
- **Comportamento:** Salva/atualiza `WORKSPACE/config/providers.json` com `provider.enabled = true` e `api_key` se fornecido.
- **Resposta:** `{"ok": true, "provider": str}`

---

### brain_repo.py (Blueprint `brain_repo`)

#### `GET /api/brain-repo/status`
- **Auth:** `@login_required`
- **Resposta:** `BrainRepoConfig.to_dict()` ou `{"connected": false}` se não configurado.

#### `POST /api/brain-repo/connect`
- **Body:** `{"token": str, "repo_url": str}` OU `{"token": str, "create_repo": str}`
- **Comportamento:**
  1. Valida PAT via `brain_repo.github_api.validate_pat_scopes` (fallback gracioso se ImportError)
  2. Se `create_repo`: cria repo privado via `github_api.create_private_repo`
  3. Se `repo_url`: valida privacidade via `github_api.validate_repo_is_private`
  4. Encripta token via `brain_repo.pat_auth.PATAuthProvider` (fallback raw bytes)
  5. Upsert `BrainRepoConfig`
- **Resposta:** `BrainRepoConfig.to_dict()`

#### `POST /api/brain-repo/disconnect`
- Remove `github_token_encrypted` (seta `None`) e `sync_enabled = False`.
- **Resposta:** `{"ok": true}`

#### `GET /api/brain-repo/detect`
- **Query param (opcional):** `?token=<PAT>` (usa stored token se não fornecido)
- **Comportamento:** Chama `github_api.detect_brain_repos(token)`, fallback `[]`
- **Resposta:** `{"repos": [...]}`

#### `GET /api/brain-repo/snapshots`
- Requer config conectado. Chama `github_api.list_snapshots(token, owner, name)`.
- **Resposta:** `{"daily": [...], "weekly": [...], "milestones": [...], "head": ...}`

#### `POST /api/brain-repo/restore/start`
- **Body:** `{"ref": str, "include_kb": bool}`
- **Content-Type resposta:** `text/event-stream` (SSE)
- **Ver seção SSE abaixo**

#### `POST /api/brain-repo/sync/force`
- **Comportamento:** `git_ops.commit_all` → `git_ops.push` → `git_ops.create_tag("milestone/manual-YYYY-MM-DD-HH-MM")`
- Atualiza `config.last_sync`, limpa `config.last_error`
- **Resposta:** `{"ok": true, "committed": bool, "tag": "milestone/manual-..."}`

#### `POST /api/brain-repo/tag/milestone`
- **Body:** `{"name": str}`
- **Comportamento:** Cria tag `milestone/<name>` via `git_ops.create_tag`
- **Resposta:** `{"ok": true, "tag": "milestone/<name>"}`

---

## Comportamento esperado do SSE stream (`/api/brain-repo/restore/start`)

O endpoint retorna `Content-Type: text/event-stream` com `Cache-Control: no-cache` e `X-Accel-Buffering: no`.

Cada evento é emitido como:
```
data: {"step": "<step_name>", "progress": <0-100>, "message": "...", "error": false}\n\n
```

Steps esperados (definidos pelo módulo `brain_repo.restore` a criar na fase seguinte):
- `"clone"` — clonando/atualizando repo local
- `"checkout"` — extraindo ref solicitado
- `"copy"` — copiando arquivos para workspace
- `"kb"` — restaurando knowledge base (se `include_kb=true`)
- `"done"` — operação concluída (`progress: 100`)
- `"error"` — falha (`error: true`)

Em caso de `ImportError` (módulo restore não existe ainda), emite evento único `done` com `progress: 100` e aviso.

---

## O que a fase de frontend precisa saber

1. **Onboarding gate:** Após login, chamar `GET /api/auth/needs-onboarding`. Se `needs_onboarding: true`, redirecionar para wizard. A rota é pública (não precisa de auth).

2. **Restaurar wizard step:** `GET /api/onboarding/state` retorna tudo que o frontend precisa para restaurar o step correto (ex: se `brain_repo_configured: false`, mostrar step de brain repo).

3. **SSE no restore:** Usar `EventSource` ou `fetch + ReadableStream`. O stream termina naturalmente após o evento `done` ou `error`.

4. **Desconectar brain repo:** `POST /api/brain-repo/disconnect` limpa credenciais mas mantém o registro — o frontend pode re-exibir o step de conexão.

5. **Detect para sugestão:** `GET /api/brain-repo/detect?token=<PAT>` pode ser chamado ao digitar o token para sugerir repos existentes.

6. **Modulos ainda não criados:** `brain_repo.github_api`, `brain_repo.pat_auth`, `brain_repo.restore` — todos têm fallback gracioso via `try/except ImportError`. O frontend não será impactado; os endpoints respondem com dados stub ou vazios.

---

## Dependências externas pendentes (fase seguinte)

| Módulo | Uso |
|---|---|
| `brain_repo.github_api` | `validate_pat_scopes`, `create_private_repo`, `validate_repo_is_private`, `detect_brain_repos`, `list_snapshots` |
| `brain_repo.pat_auth` | `PATAuthProvider` — encrypt/decrypt token via Fernet + `BRAIN_REPO_MASTER_KEY` |
| `brain_repo.restore` | `execute_restore(token, repo_url, local_path, ref, include_kb)` → generator de dicts SSE |
