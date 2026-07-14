# ═══════════════════════════════════════════════════════════════════════════
# RELATÓRIO DE DIAGNÓSTICO — EvoNexus
# Gerado: 2026-06-16
# ═══════════════════════════════════════════════════════════════════════════

## 1. RESUMO EXECUTIVO

| Métrica | Valor |
|---------|-------|
| Total de heartbeat runs (all time) | 421 |
| Success | 294 (70%) |
| Fail | 123 (29%) |
| Running (presos) | 4 |
| Heartbeats habilitados | 21 |
| Agentes com falha recorrente | 10+ |

**Problema #1**: 29% de falha nos heartbeats — a maioria por "exit code 1" (erro genérico do openclaude).
**Problema #2**: zara-2h lidera com 20 falhas (nunca conseguiu completar).
**Problema #3**: 4 heartbeats "running" travados no DB (zombie runs).
**Problema #4**: Nenhum mecanismo de retry inteligente — quando falha, só alerta no Telegram.

---

## 2. FALHAS POR HEARTBEAT (hoje, 2026-06-16)

### 2.1. Falhas CRÍTICAS (100% fail, nunca success)

| Heartbeat | Fails | Causa | Ação |
|-----------|-------|-------|------|
| autopilot-bolt-executor | 10/10 | exit code 1 (openclaude crash) | Ver agent file, provavelmente .md corrompido |
| autopilot-compass-planner | 5/5 | exit code 1 | Ver agent file |
| autopilot-grid-tester | 5/5 | exit code 1 | Ver agent file |
| autopilot-raven-critic | 5/5 | exit code 1 | Ver agent file |
| autopilot-vault-security | 5/5 | exit code 1 | Ver agent file |

### 2.2. Falhas PARCIAIS (mix de success/fail)

| Heartbeat | Fails | Success | Causa | Ação |
|-----------|-------|---------|-------|------|
| zara-2h | 20 | 3 | "unknown option '---" | Agent .md corrompido (parse error) |
| autopilot-oath-verifier | 8 | 2 | exit code 1 | Ver agent file |
| autopilot-pixel-social-media | 7 | 2 | exit code 1 | Ver agent file |
| autopilot-hawk-debugger | 7 | 4 | exit code 1 | Ver agent file |
| autopilot-helm-conductor | 6 | 4 | exit code 1 | Ver agent file |
| autopilot-mako-marketing | 6 | 5 | exit code 1 | Ver agent file |
| autopilot-sage-strategy | 6 | 5 | context window warning + exit code 1 | Adicionar modelo na context window table |
| autopilot-quill-writer | 5 | 6 | exit code 1 | Ver agent file |
| autopilot-scout-explorer | 5 | 5 | context window warning + exit code 1 | Adicionar modelo na context window table |
| autopilot-clawdia-assistant | 4 | 1 | exit code 1 | Ver agent file |
| autopilot-dex-data | 4 | 1 | context window warning + exit code 1 | Adicionar modelo na context window table |

### 2.3. Funcionando OK

| Heartbeat | Success | Observação |
|-----------|---------|------------|
| integrations-health | 31 | 100% |
| zara-2h | 3 | 3/23 — quase sempre falha |

---

## 3. CAUSAS RAIZ IDENTIFICADAS

### 3.1. "exit code 1" genérico (maioria das falhas)

O `heartbeat_runner.py` chama `openclaude --print --max-turns N --dangerously-skip-permissions --output-format json -- <prompt>`. Quando o agente .md tem conteúdo mal-formado (ex: YAML frontmatter quebrado, caracteres especiais não-escapados), o openclaude retorna exit code 1 sem output legível.

**Padrão**: heartbeats que NUNCA dão success (bolt, compass, grid, raven, vault) provavelmente têm .md corrompido.

**Solução**: Script de validação que lê cada .claude/agents/*.md e tenta parse do frontmatter + body.

### 3.2. "context window warning" (sage, scout, clawdia, dex)

O modelo `minimaxai/minimax-m3` não está na tabela de context windows do openclaude. Isso causa warning repetido e pode levar a truncation silencioso → resposta vazia → exit code 1.

**Solução**: Adicionar `minimaxai/minimax-m3` à context window table.

### 3.3. zara-2h: "unknown option '---"

O agent file do zara-cs provavelmente tem conteúdo que o openclaude interpreta como CLI option em vez de system prompt. O `--` separator pode estar sendo mal-parsado.

**Solução**: Revisar `.claude/agents/zara-cs.md` e limpar caracteres especiais.

### 3.4. 4 heartbeats "running" travados

Zombie runs no DB que nunca completam. Provavelmente heartbeats que foram interrompidos (processo morto) sem cleanup.

**Solução**: Script de limpeza que marca runs "running" com mais de 2h como "timeout".

### 3.5. Sem retry inteligente

Quando um heartbeat falha, não há retry. O próximo disparo só acontece no próximo intervalo (15min a 6h). Para heartbeats críticos, isso é inaceitável.

**Solução**: Implementar retry com backoff exponencial (1min, 2min, 4min) antes de desistir.

### 3.6. Sem report de sucesso

Hoje só recebemos alertas de falha no Telegram. Não há visibilidade do que está funcionando.

**Solução**: Já implementado `_step_report_success` — precisa ser deployado.

### 3.7. Concorrência → 429

Múltiplos heartbeats disparando simultaneamente contra o mesmo modelo NVIDIA → burst 429.

**Solução**: Já implementado per-model inflight lock — precisa ser deployado.

---

## 4. PLANO DE AÇÃO (ordem de prioridade)

### P0 — CRÍTICO (faz agora)

1. **Validar todos os agent .md files** — script que identifica corrompidos
2. **Limpar zombie runs** — marca runs "running" >2h como "timeout"
3. **Adicionar minimax-m3 na context window table** — elimina warning
4. **Corrigir zara-cs.md** — remover caracteres que quebram parsing
5. **Reiniciar app.py** — aplica per-model lock + success reports

### P1 — ALTO (faz esta semana)

6. **Retry inteligente** — 3 tentativas com backoff exponencial por heartbeat
7. **Script de validação de agentes** — roda no scheduler semanalmente
8. **Mover heartbeats 100% fail para disabled** — evita ruído (bolt, compass, grid, raven, vault)

### P2 — MÉDIO (faz este mês)

9. **Goal "EvoNexus Self-Heal"** — goal de sistema que auto-corrige problemas
10. **Dashboard de saúde** — página que mostra success rate por heartbeat
11. **Alerta de degradação** — se success rate < 50% em 24h, alerta

---

## 5. DADOS TÉCNICOS

### 5.1. Chain NVIDIA (11 modelos, pós-remoção v4-pro)

```
1.  minimaxai/minimax-m3
2.  stepfun-ai/step-3.7-flash
3.  moonshotai/kimi-k2.6
4.  deepseek-ai/deepseek-v4-flash
5.  z-ai/glm-5.1
6.  nvidia/nemotron-3-ultra-550b-a55b
7.  nvidia/nemotron-3-super-120b-a12b
8.  qwen/qwen3.5-122b-a10b
9.  qwen/qwen3.5-397b-a17b
10. openai/gpt-oss-120b
11. microsoft/phi-4-multimodal-instruct
12. stepfun-ai/step-3.5-flash
```

### 5.2. Rotinas (metrics.json)

| Rotina | Runs | Success Rate | Custo |
|--------|------|-------------|-------|
| uso_modelos_dia | 18 | 83% | $0.0014 |
| ai-news-daily-sage | 4 | 100% | $3.19 |
| memory-sync | 12 | 92% | $0.66 |
| good-morning | 10 | 90% | $0.62 |
| end-of-day | 10 | 100% | $0.23 |

### 5.3. Processos

| Processo | PID | Status |
|----------|-----|--------|
| scheduler.py | 2079434 | ✅ running |
| app.py (Flask) | 3653906 | ✅ running |
| terminal-server | 3595718 | ✅ running |
| uso_modelos_dia | 3662520 | ✅ completed (9/12 OK) |
