# Handoff: Restore VPS, Backup SQLite e Plugins 500

Data: 2026-07-05  
Status: em andamento

## 1. Objetivo

Restaurar corretamente o EvoNexus na VPS a partir de um backup local, sem perder integrações, plugins, dados do dashboard e workspace. Durante a restauração foi descoberto que o backup atual pode capturar bancos SQLite em estado inconsistente por copiar `*.db` sem incluir/aplicar WAL, causando `database disk image is malformed` no container. Também surgiu um erro novo: a área de plugins está retornando `500 Internal Server Error`, e localmente a aplicação está pedindo criação de usuário, indicando divergência de DB/config entre ambiente local e VPS.

## 2. Contexto essencial

- Repositório local: `/home/sistemabritto/Documentos/evo-nexus`.
- Stack VPS: Docker Swarm, serviço principal `evonexus_evonexus_dashboard`.
- Imagens usadas na VPS:
  - `excarplex/evo-nexus-dashboard:latest`
  - `excarplex/evo-nexus-runtime:latest`
- Stack file local relevante: `evonexus-vps.stack.yml`.
- O dashboard usa SQLite em `/workspace/dashboard/data/evonexus.db`.
- Na VPS, o mount real do DB do dashboard é:
  - `evonexus_evonexus_dashboard_data -> /workspace/dashboard/data`
- A configuração persistente e integrações passam por `/workspace/config/.env`.
- O entrypoint faz symlink:
  - `/workspace/.env -> /workspace/config/.env`
- Portanto, o `.env` correto em Swarm é o do volume `config`, não um arquivo solto dentro da imagem.
- O stack monta `/workspace/config`, mas não monta `/workspace/.env` diretamente. Isso é esperado porque o entrypoint cria o symlink.
- Integrações core são avaliadas por variáveis de ambiente carregadas de `.env`, conforme `dashboard/backend/routes/integrations.py`.
- O backup local atual inclui `.env` e `dashboard/data/evonexus.db`, mas o script atual exclui `.db-wal` e `.db-shm`.
- Isso é perigoso quando SQLite está em WAL: copiar só o `.db` pode gerar backup sem transações recentes ou até malformado.
- O usuário pediu para corrigir o backup e fazer push para GitHub, mas interrompeu antes da edição.
- O usuário agora quer handoff para continuar com Fable/outra sessão.

## 3. O que já foi feito

1. Analisamos logs iniciais da VPS.
   - O endpoint `POST /api/backups/<zip>/restore` retornava `202`.
   - Isso significa apenas “job aceito”, não “restore concluído”.

2. Foi identificado um bug de UX/API no restore:
   - `dashboard/backend/routes/backups.py` guarda `_running_jobs["restore"]`.
   - Mas `/api/backups/status` retorna só `_running_jobs["backup"]`.
   - Resultado: o frontend não mostra erro/conclusão real do restore.

3. Foi identificado que o frontend usa restore em modo `merge` por padrão.
   - Em `merge`, arquivos existentes não são sobrescritos.
   - Isso explicava parcialmente “subiu mas não apareceu nada”.
   - O usuário refez via browser com `replace`.

4. O restore via browser continuou sem visibilidade real.
   - Foi recomendado rodar restore manual dentro do container.

5. Na VPS, `docker ps` mostrou container do dashboard:
   - ID inicial usado: `59573e9af77c`
   - Nome: `evonexus_evonexus_dashboard.1...`

6. Descobrimos que a imagem da VPS não contém `/workspace/backup.py`.
   - Existe apenas `/workspace/ADWs/routines/backup.py`.
   - Então foi usado um script Python inline para extrair o ZIP diretamente.

7. O usuário restaurou o ZIP manualmente dentro do container:
   - ZIP: `/workspace/backups/evonexus-backup-20260704-203851.zip`
   - Saída:
     - `created_at: 2026-07-04T20:38:51.761046`
     - `file_count: 580`
     - `RESTORE COMPLETE`
     - `restored: 580`

8. Após restart do serviço, o dashboard entrou em crash loop.
   - Logs mostraram:
     - `sqlite3.DatabaseError: database disk image is malformed`
     - SQL: `PRAGMA main.table_info("users")`
   - Causa provável: `evonexus.db` restaurado veio inconsistente/corrompido.

9. Tentamos recuperar SQLite com `.recover`.
   - Primeiro foi usado o volume errado por suspeita de nome.
   - Depois o usuário inspecionou mounts e confirmou o volume correto:
     - `evonexus_evonexus_dashboard_data -> /workspace/dashboard/data`

10. O usuário pausou o dashboard:
    - `docker service scale evonexus_evonexus_dashboard=0`

11. O usuário rodou recovery no volume correto.
    - Houve um erro intermediário por comando quebrado em múltiplas linhas e por falta de espaço em `sqlite3 evonexus.db ".recover"`.
    - Isso gerou um `evonexus.db` vazio e a aplicação passou a pedir setup/criação de usuário.
    - Esse caminho foi descartado porque zerou o DB.

12. Recuperamos a partir do backup `evonexus.db.bad.manual`.
    - Comando usado:
      ```bash
      docker run --rm -v evonexus_evonexus_dashboard_data:/data alpine sh -lc 'apk add --no-cache sqlite >/dev/null; cd /data; rm -f evonexus.db evonexus.db-wal evonexus.db-shm; sqlite3 evonexus.db.bad.manual ".recover" | sqlite3 evonexus.db; sqlite3 evonexus.db "PRAGMA integrity_check;"; ls -lh evonexus.db evonexus.db.bad.manual; sqlite3 evonexus.db ".tables"; sqlite3 evonexus.db "select count(*) from users;"'
      ```
    - Saída relevante:
      - `ok`
      - tabelas presentes, incluindo `users`, `plugins_installed`, `runtime_configs`, `brain_repo_configs`, `knowledge_connections`
      - `select count(*) from users;` retornou `1`

13. O dashboard foi escalado de volta:
    ```bash
    docker service scale evonexus_evonexus_dashboard=1
    ```
    - Logs mostraram Flask subindo corretamente.
    - O erro `database disk image is malformed` sumiu.

14. O usuário informou que “subiu uma parte”, mas integrações ainda não voltaram.
    - Foi analisado o código local e confirmado que integrações dependem do `.env`.
    - O `entrypoint.sh` carrega `/workspace/config/.env`.
    - O backup atual provavelmente incluiu `.env` no caminho raiz, mas em Swarm o caminho persistente real é `config/.env` via symlink.

15. Foi iniciada análise local para corrigir `backup.py`.
    - `backup.py` atual coleta arquivos ignorados pelo git com:
      - walk dinâmico de `workspace`, `memory`, `plugins`
      - `git ls-files --others --ignored --exclude-standard`
    - `.env`, `config/workspace.yaml`, `dashboard/data/evonexus.db` são ignorados pelo git e entram no backup.
    - `.db-wal` e `.db-shm` são explicitamente excluídos por `EXCLUDE_EXTENSIONS`.
    - Localmente existe:
      - `dashboard/data/evonexus.db`
      - `dashboard/data/evonexus.db-wal`
      - `dashboard/data/mempalace/chroma.sqlite3`
    - `dashboard/data/evonexus.db` local passa `PRAGMA integrity_check`.
    - `dashboard/data/mempalace/chroma.sqlite3` local passa `PRAGMA integrity_check`.

16. Foi preparado o plano de correção, mas nenhuma alteração foi aplicada antes da interrupção:
    - Alterar `backup.py` para gerar snapshots consistentes de SQLite usando `sqlite3.Connection.backup()`.
    - Ao zipar arquivos SQLite vivos, escrever a cópia snapshot no ZIP, não o arquivo `.db` direto.
    - Manter exclusão de `.db-wal` e `.db-shm`, desde que o snapshot incorpore WAL corretamente.
    - Stagear e commitar apenas `backup.py` porque o worktree tem muitas mudanças não relacionadas.

17. O usuário adicionou novo problema:
    - Plugins retornando:
      - `500 INTERNAL SERVER ERROR`
      - HTML padrão do Flask.
    - Localhost pedindo criação de usuário.
    - Essa parte ainda não foi investigada.

## 4. Estado atual

- VPS:
  - Dashboard voltou a subir depois do SQLite recovery.
  - Último log bom visto:
    - Flask rodando em `0.0.0.0:8080`
    - `/api/version` retornando `200`
  - O banco recuperado tem pelo menos 1 usuário.
  - Parte dos dados aparece.
  - Integrações ainda não aparecem corretamente.
  - Plugins agora dão `500 Internal Server Error` segundo o usuário.

- Local:
  - Repo em `/home/sistemabritto/Documentos/evo-nexus`.
  - Branch atual no momento da análise:
    - `feat/chat-openclaude-provider-routing`
  - Remote:
    - `origin` e `fork` apontam para `github.com/sistemabritto/evo-nexus.git`
  - `gh auth status` estava autenticado como `sistemabritto`.
  - Worktree está sujo com muitas mudanças não relacionadas.
  - Importante: não usar `git add -A`.
  - Apenas `backup.py` deve ser stageado para o fix de backup, salvo se o próximo agente fizer correções adicionais específicas.

- Problemas confirmados:
  - Backup atual pode gerar SQLite inconsistente.
  - Restore via browser não expõe status/erro real de restore.
  - Integrações dependem de `/workspace/config/.env` no Swarm.
  - Plugins 500 ainda sem diagnóstico.
  - Localhost pedindo criação de usuário indica DB local ausente/zerado/inconsistente ou app apontando para outro `dashboard/data/evonexus.db`.

## 5. Próximos passos

1. Não criar usuário novo ainda, nem local nem VPS, até entender qual DB/config está sendo lido.

2. Diagnosticar plugins 500 na VPS:
   ```bash
   docker service logs evonexus_evonexus_dashboard --since 20m 2>&1 | grep -iE "plugin|traceback|exception|error|500" -A20 -B10
   ```
   Também abrir a rota que falha e olhar traceback completo:
   ```bash
   docker service logs -f evonexus_evonexus_dashboard
   ```
   Depois acessar Plugins no browser para capturar a exceção.

3. Confirmar se as integrações estão no `.env` persistente da VPS:
   ```bash
   docker run --rm -v evonexus_evonexus_config:/config alpine sh -lc 'ls -lah /config && sed -n "1,220p" /config/.env'
   ```
   Atenção: esse comando exibe segredos. Não colar output completo em canais públicos. Se precisar compartilhar, mascarar tokens.

4. Conferir se o ZIP restaurado continha `.env` e/ou `config/.env`:
   ```bash
   docker run --rm -v evonexus_evonexus_backups:/backups alpine sh -lc 'apk add --no-cache unzip >/dev/null; unzip -l /backups/evonexus-backup-20260704-203851.zip | grep -E "(^| )(\.env|config/\.env)$"'
   ```
   Se só havia `.env`, o restore manual escreveu `/workspace/.env` no container, mas em Swarm o symlink aponta para `/workspace/config/.env`. É necessário garantir que backup e restore preservem `config/.env`.

5. Corrigir `backup.py` local para snapshot SQLite consistente.
   Implementação sugerida:
   - importar `sqlite3`;
   - criar helper `_is_sqlite_file(path: Path)`;
   - criar helper `_write_sqlite_snapshot_to_zip(zf, src, rel, tmpdir)`;
   - usar `sqlite3.connect(f"file:{src}?mode=ro", uri=True)` e `src_conn.backup(dst_conn)`;
   - escrever snapshot temporário no ZIP com o mesmo `rel`;
   - para SQLite inválido ou vazio, cair para `zf.write` com aviso, ou falhar explicitamente para bancos críticos.

6. Ajustar coleta de env para Swarm:
   - Garantir que `config/.env` entre no backup se existir.
   - Hoje `git check-ignore` indica que `config/.env` é ignorado por regra `.env`, então deve entrar via `git ls-files --others --ignored`.
   - Mesmo assim, validar manifesto do próximo backup para confirmar.

7. Gerar backup novo local após corrigir script.
   - Ideal: parar serviços locais que escrevem SQLite antes do backup.
   - Se estiver usando Docker Compose local:
     ```bash
     docker compose stop || true
     python backup.py backup
     docker compose start || true
     ```
   - Se estiver rodando app local sem Docker, parar o processo Flask/terminal antes.
   - Validar ZIP gerado:
     ```bash
     python - <<'PY'
     from pathlib import Path
     import json, zipfile, sqlite3, tempfile
     zips = sorted(Path("backups").glob("evonexus-backup-*.zip"))
     p = zips[-1]
     print(p)
     with zipfile.ZipFile(p) as z:
         m = json.loads(z.read("manifest.json"))
         files = [e["path"] for e in m["files"]]
         for target in [".env", "config/.env", "dashboard/data/evonexus.db", "dashboard/data/mempalace/chroma.sqlite3"]:
             print(target, target in files)
         with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
             tmp.write(z.read("dashboard/data/evonexus.db"))
             tmp.flush()
             con = sqlite3.connect(tmp.name)
             print("evonexus integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
             print("users:", con.execute("select count(*) from users").fetchone()[0])
             con.close()
     PY
     ```

8. Testar `backup.py` com uma execução real.
   - O backup novo deve não gerar DB malformado.
   - Conferir tamanho e manifesto.

9. Commit e push:
   - Usar apenas `backup.py` se for só o fix de backup.
   - Como worktree está misto:
     ```bash
     git diff -- backup.py
     git add backup.py
     git commit -m "Fix consistent SQLite backup snapshots"
     git push -u fork feat/chat-openclaude-provider-routing
     ```
   - Se preferir branch nova para esse fix:
     ```bash
     git switch -c codex/fix-sqlite-backup-snapshots
     git add backup.py
     git commit -m "Fix consistent SQLite backup snapshots"
     git push -u fork codex/fix-sqlite-backup-snapshots
     ```
   - Não stagear arquivos não relacionados.

10. Depois de gerar backup novo, restaurar na VPS com mais cuidado:
    - Escalar serviços que escrevem para os volumes para 0:
      ```bash
      docker service scale evonexus_evonexus_dashboard=0
      docker service scale evonexus_evonexus_scheduler=0
      docker service scale evonexus_evonexus_telegram=0
      ```
    - Subir ZIP novo para volume `evonexus_evonexus_backups`.
    - Restaurar com script robusto que respeite symlink/env:
      - Para arquivos `config/.env`, escrever em `/workspace/config/.env`.
      - Para `.env`, decidir se deve copiar para `config/.env` no Swarm.
    - Subir serviços novamente.

11. Corrigir bug do status de restore no backend/frontend.
    - Backend: `/api/backups/status` deve aceitar `?type=restore` ou retornar ambos `backup` e `restore`.
    - Frontend: quando `handleRestore`, pollar status de restore, não backup.
    - Logar exceções de thread com traceback, não só `str(e)`.

## 6. Perguntas em aberto

- O backup novo deve incluir os dois caminhos `.env` e `config/.env`, ou normalizar para `config/.env` no Swarm?
- O restore web deve sobrescrever `.env`/`config/.env` automaticamente? Isso pode quebrar produção se o backup veio de local com URLs/keys diferentes.
- O erro 500 de plugins vem do DB recuperado, de plugin instalado ausente no volume `plugins`, de schema antigo, ou de config `.env` faltante?
- O usuário quer preservar exatamente o usuário/login antigo ou aceita recriar usuário se os dados principais forem recuperados?
- O backup deve falhar quando banco crítico SQLite não passar `integrity_check`, ou deve incluir com warning?
- Deve haver comando oficial de restore para imagens Swarm, já que `/workspace/backup.py` não existe no container `excarplex/evo-nexus-dashboard:latest`?

## 7. Artefatos relevantes

### Arquivos locais

- `backup.py`
  - Script de backup/restore local.
  - Precisa ser corrigido para snapshots SQLite.

- `evonexus-vps.stack.yml`
  - Stack Swarm.
  - Mostra volumes persistentes e environment.

- `entrypoint.sh`
  - Cria `/workspace/config/.env`.
  - Gera `EVONEXUS_SECRET_KEY` e `KNOWLEDGE_MASTER_KEY` se faltarem.
  - Symlinka `/workspace/.env` para `/workspace/config/.env`.
  - Faz source do `.env`.

- `dashboard/backend/routes/backups.py`
  - Endpoint de backup/restore.
  - Bug: status expõe backup, não restore.

- `dashboard/backend/routes/integrations.py`
  - Integrações core dependem de env vars.
  - Custom/plugin integrations também avaliam env vars.

- `dashboard/backend/routes/plugins.py`
  - Provável ponto para investigar 500 de plugins.

- `dashboard/backend/models.py`
  - Tabelas relevantes: `users`, `plugins_installed`, `runtime_configs`, `brain_repo_configs`, `knowledge_connections`.

### Comandos usados na VPS

Confirmar mounts do serviço:
```bash
docker service inspect evonexus_evonexus_dashboard --format '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{println .Source "->" .Target}}{{end}}'
```

Saída relevante:
```text
evonexus_evonexus_dashboard_data -> /workspace/dashboard/data
evonexus_evonexus_config -> /workspace/config
evonexus_evonexus_workspace -> /workspace/workspace
evonexus_evonexus_memory -> /workspace/memory
evonexus_evonexus_agent_memory -> /workspace/.claude/agent-memory
```

Recovery que funcionou:
```bash
docker service scale evonexus_evonexus_dashboard=0
docker run --rm -v evonexus_evonexus_dashboard_data:/data alpine sh -lc 'apk add --no-cache sqlite >/dev/null; cd /data; rm -f evonexus.db evonexus.db-wal evonexus.db-shm; sqlite3 evonexus.db.bad.manual ".recover" | sqlite3 evonexus.db; sqlite3 evonexus.db "PRAGMA integrity_check;"; ls -lh evonexus.db evonexus.db.bad.manual; sqlite3 evonexus.db ".tables"; sqlite3 evonexus.db "select count(*) from users;"'
docker service scale evonexus_evonexus_dashboard=1
```

Saída relevante:
```text
defensive off
ok
users
1
```

Logs bons após recovery:
```text
Serving Flask app 'app'
Running on http://127.0.0.1:8080
GET /api/version HTTP/1.1" 200
```

Erro anterior:
```text
sqlite3.DatabaseError: database disk image is malformed
sqlalchemy.exc.DatabaseError: [SQL: PRAGMA main.table_info("users")]
```

### Estado Git local no momento da análise

Branch:
```text
feat/chat-openclaude-provider-routing
```

Worktree tinha muitas mudanças não relacionadas. Exemplos:
```text
 M .claude/skills/create-goal/SKILL.md
 M ADWs/routines/memory_sync.py
 M Makefile
 M dashboard/backend/app.py
 M dashboard/backend/models.py
 M dashboard/backend/notifications.py
 M dashboard/backend/routes/overview.py
 M dashboard/terminal-server/src/claude-bridge.js
 M scripts/post_to_x.py
 M start-services.sh
?? dashboard/backend/routes/instagram.py
?? tests/backend/test_mempalace_routes.py
```

Não stagear tudo.

## 8. Instruções pra próxima sessão

- Falar em português com o usuário, direto e operacional.
- O usuário está em modo incidente; priorize comandos prontos e curtos.
- Evitar comandos multiline complexos na VPS porque o terminal dele quebrou heredocs e linhas longas com indentação.
- Preferir comandos de uma linha quando o usuário for copiar para VPS.
- Não pedir para ele colar `.env` completo sem mascarar segredos.
- Não mandar criar usuário novo enquanto houver chance de o app estar apontando para DB errado.
- Não usar `git add -A`; o worktree local tem várias mudanças de outros trabalhos.
- Antes de qualquer push, revisar `git diff -- backup.py` e stagear só o escopo.
- Corrigir primeiro a causa raiz do backup SQLite. Sem isso, repetir restore pode recriar o problema.
- Para o 500 de plugins, buscar traceback real nos logs antes de editar código.
- Lembrar que a imagem Swarm atual não tem `/workspace/backup.py`; qualquer procedimento de restore na VPS precisa usar script inline, incluir `backup.py` na imagem, ou restaurar via UI corrigida.
- Se for fazer PR, usar draft PR e explicar claramente:
  - root cause: SQLite copiado enquanto WAL ativo;
  - fix: snapshot consistente via SQLite backup API;
  - impacto: backups restauráveis e menos risco de DB malformado.
