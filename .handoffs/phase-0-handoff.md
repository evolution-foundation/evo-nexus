# Phase 0 Handoff — Audit + Repo Setup

## O que foi feito
- Branch `feat/brain-repo-onboarding` criada a partir de `upstream/develop` (v0.29.0)
- Git config: `user.email=16343265+NeritonDias@users.noreply.github.com`
- Auditoria completa de arquivos críticos concluída

## Arquivos tocados
- Criados: `.handoffs/phase-0-handoff.md`, `.handoffs/domain-auth.md`, `.handoffs/domain-watcher.md`
- Criados: `dashboard/backend/models.py` (modificado — colunas + BrainRepoConfig), `dashboard/backend/app.py` (modificado — migration block + BRAIN_REPO_MASTER_KEY)
- Criado: `dashboard/backend/brain_repo/__init__.py`

## Decisões tomadas (desvios do plano)

### 1. SQLite (não Postgres) para o app DB
O `app.py` usa SQLite via `SQLALCHEMY_DATABASE_URI = sqlite:///...`. A migração via Postgres do plano original não se aplica. O padrão de migração é: raw `sqlite3` `ALTER TABLE ADD COLUMN` no bloco de startup do `app.py`. Seguir esse padrão exatamente.

### 2. Sem modelo Workspace
Não existe modelo `Workspace` — workspace é arquivo `config/workspace.yaml`. O `BrainRepoConfig` usa `user_id` FK para `users`.

### 3. Brain routines em `ADWs/routines/custom/`
Scheduler (`_load_custom_routines`) só reconhece rotinas configuráveis via `routines.yaml` se estiverem em `ADWs/routines/custom/`. Rotinas brain_* vão em `ADWs/routines/custom/brain_*.py` com `interval: 5` ou `interval: 15` (minutos). tags diária/semanal usam `time: "23:59"`.

### 4. `cryptography` já disponível
Fernet disponível — `cryptography>=42` já está em `pyproject.toml`. Watchdog adicionado nesta fase.

### 5. Migration pattern em app.py
Seguir EXATAMENTE o padrão existente:
```python
_user_cols = {row[1] for row in _cur.execute("PRAGMA table_info(users)").fetchall()}
if "onboarding_state" not in _user_cols:
    _cur.execute("ALTER TABLE users ADD COLUMN onboarding_state TEXT")
    _conn.commit()
```

## Design tokens (Login.tsx)
```
bg-page:     #080c14
bg-card:     #0b1018
bg-input:    #0f1520
border-card: #152030
border-input: #1e2a3a
accent:      #00FFA7
accent-hover: #00e69a
text-main:   #e2e8f0
text-label:  #5a6b7f
text-subtle: #4a5a6e
text-dim:    #2d3d4f
error-bg:    #1a0a0a
error-border: #3a1515
error-text:  #f87171
```

**Container padrão:** `w-full max-w-[380px] relative z-10`  
**Card padrão:** `rounded-xl border border-[#152030] bg-[#0b1018] shadow-[0_4px_40px_rgba(0,0,0,0.4)]`  
**Input padrão:** `w-full px-4 py-3 rounded-lg bg-[#0f1520] border border-[#1e2a3a] text-[#e2e8f0] placeholder-[#3d4f65] text-sm focus:outline-none focus:border-[#00FFA7]/60 focus:ring-1 focus:ring-[#00FFA7]/20`  
**Label padrão:** `text-[11px] font-semibold text-[#5a6b7f] mb-1.5 tracking-[0.08em] uppercase`  
**Botão primário:** `bg-[#00FFA7] text-[#080c14] hover:bg-[#00e69a] py-3 rounded-lg text-sm font-semibold`  
**Fundo animado:** `<NetworkCanvas />` — canvas de partículas verdes #00FFA7 com `fixed inset-0`  
**Font:** `font-[Inter,-apple-system,sans-serif]`  
**Para wizard com mais de 380px:** max-w pode ser aumentado para `max-w-[480px]` ou `max-w-[520px]`

## Gate de auditoria
- [x] Branch configurada
- [x] Scheduler suporta `interval` (minutos) via routines.yaml
- [x] Fernet disponível
- [x] SQLite confirmado (não Postgres)
- [x] Design tokens documentados

## O que a próxima fase precisa saber
- Padrão de migração: `PRAGMA table_info(users)` → `ALTER TABLE ADD COLUMN`
- `WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent` em `routes/_helpers.py`
- Blueprints são registrados em `app.py` com `app.register_blueprint(bp)`
- `PUBLIC_PATHS` em `app.py` — adicionar `/api/auth/needs-onboarding` lá
- `needs_setup()` em `models.py` é o padrão para `needs_onboarding()`
- API lib do frontend: `api.get('/path')`, `api.post('/path', body)` — usa `/api` prefix internamente
- SSE (Server-Sent Events) para restore: usar `stream_with_context` do Flask
