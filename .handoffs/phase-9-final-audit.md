# Phase 9 — Final Audit

## Estado: APROVADO

## Checklist completo

### Parse / Build
- [x] `models.py` — OK
- [x] `app.py` — OK
- [x] `setup.py` — OK
- [x] `backup.py` — OK
- [x] Todos os 12 módulos em `dashboard/backend/brain_repo/` — OK
- [x] Todas as routes novas (`onboarding.py`, `brain_repo.py`, `auth_routes.py`) — OK
- [x] Todas as 5 rotinas custom (`ADWs/routines/custom/brain_*.py`) — OK
- [x] `npm run build` — limpo (zero erros TypeScript)

### Segurança
- [x] Nenhum token/secret real em nenhum commit
- [x] `secrets_scanner.py` com 21 patterns
- [x] `BRAIN_REPO_MASTER_KEY` auto-gerado, nunca commitado
- [x] Token GitHub: criptografado via Fernet antes de salvar no banco

### Funcionalidade
- [x] `OracleWelcomeBanner` importado em `Agents.tsx`, renderiza condicionalmente
- [x] Guard de onboarding em `App.tsx` (`/onboarding/*` rota)
- [x] `/api/auth/needs-onboarding` em `PUBLIC_PATHS` do `app.py`
- [x] `memory/raw-transcripts/` adicionado ao `.gitignore`
- [x] Brain routines em `ADWs/routines/custom/` (não em raiz — configuráveis via YAML)

### Traduções
- [x] `brainRepo` namespace adicionado nos 3 bundles (en-US, pt-BR, es)
- [x] 14 keys em paridade em todos os 3 bundles

### Arquitetura
- [x] PAT → OAuth swap: trocar `PATAuthProvider` por `OAuthAuthProvider` sem refactor
- [x] OAuth atrás de `EVO_NEXUS_GITHUB_CLIENT_ID` feature flag
- [x] Watcher: `DEBOUNCE_SECONDS` configurável via parâmetro (testável com valor menor)
- [x] Restore: secrets scan antes do swap (nunca chega arquivo com secret no install_dir)
- [x] KB import: ignorado quando `kb_key_matches=False` (não lança erro)

## Itens para atenção do operador

1. **`ADWs/routines/custom/` está no .gitignore** — brain routines foram commitadas com `git add -f`. Verificar se isso é intencional ou se o .gitignore deve ser ajustado.
2. **Watcher não inicia no servidor de desenvolvimento** — só inicia quando há `BrainRepoConfig` com `sync_enabled=True` no banco.
3. **KB mirror**: requer variáveis Postgres (`POSTGRES_HOST` etc) — retorna 0 silenciosamente se não configurado.

## Commits no branch
7 commits lógicos, todos commitados como `feat(brain-repo-onboarding)`.
