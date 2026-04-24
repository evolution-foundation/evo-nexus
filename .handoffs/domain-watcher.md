# Domain Handoff: File Watcher + Sync Worker

## Ciclo de vida

```
app.py startup
    └─ start_brain_watcher() (se brain repo configurado)
           └─ BrainRepoWatcher.start()
                    └─ watchdog.Observer thread (daemon)

Arquivo muda em memory/ workspace/ customizations/ config-safe/
    └─ _on_change(event)
           └─ reseta timer de debounce (30s)
           └─ após 30s sem novo evento: _flush()
                    └─ tenta commit + push
                    └─ sucesso: atualiza last_sync
                    └─ falha: SyncWorker.enqueue(job)

SyncWorker (thread daemon)
    └─ loop: a cada 60s processa pending/ queue
           └─ tenta push de jobs pendentes
           └─ atualiza BrainRepoConfig.pending_count
```

## Formato do job em `pending/`

```json
{
  "id": "uuid4",
  "type": "commit_push",
  "created_at": "2026-04-23T14:00:00Z",
  "attempts": 0,
  "last_error": null
}
```

Jobs ficam em `<brain_repo_local_dir>/../brain-pending/<uuid>.json` (fora do repo git para não commitar).

## Parâmetros configuráveis

- `DEBOUNCE_SECONDS`: default 30, aceitável via parâmetro em `__init__` (para testes usar 2)
- `RETRY_INTERVAL_SECONDS`: default 60
- Caminhos observados: `memory/`, `workspace/`, `customizations/`, `config-safe/`
- Caminhos excluídos: `memory/raw-transcripts/`

## Integração com app.py

```python
# app.py — após inicialização do banco
try:
    from brain_repo.watcher import start_brain_watcher
    start_brain_watcher(WORKSPACE)
except Exception as e:
    print(f"WARNING: brain watcher init failed: {e}")
```

## Status

- [ ] Fase 3: Criar `watcher.py` e `sync_worker.py`
- [ ] Fase 6: Adicionar `start_brain_watcher()` call em `app.py`
- [ ] Fase 7: Badge "pending" no frontend (usa `BrainRepoConfig.pending_count`)
