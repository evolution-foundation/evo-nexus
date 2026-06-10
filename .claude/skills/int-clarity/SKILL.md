---
name: int-clarity
description: "Query Microsoft Clarity Data Export API for session analytics, engagement, scroll depth, rage/dead clicks, and UTM attribution. Supports multiple projects via CLARITY_TOKEN_<NAME> env vars. Calls clarity.ms/export-data directly — no SDK or proxy dependency."
homepage: https://clarity.microsoft.com
metadata:
  openclaw:
    requires:
      env:
        - CLARITY_API_TOKEN
      bins:
        - python3
    primaryEnv: CLARITY_API_TOKEN
    files:
      - "scripts/*"
---

# Microsoft Clarity — Data Export API

Acesse métricas de comportamento do Clarity diretamente via API, sem SDK.

## Setup (uma vez)

1. Acesse https://clarity.microsoft.com → Settings → Export Data.
2. Gere um token de projeto.
3. Adicione ao `.env`:

   **Projeto único:**
   ```
   CLARITY_API_TOKEN=seu_token_aqui
   ```

   **Múltiplos projetos (padrão recomendado):**
   ```
   CLARITY_TOKEN_ACME=token_acme
   CLARITY_TOKEN_GLOBEX=token_globex
   CLARITY_TOKEN_INITECH=token_initech
   ```

   > `--project ACME` seleciona `CLARITY_TOKEN_ACME`.
   > Sem `--project`: usa `CLARITY_API_TOKEN` como fallback.

   **Base URL (opcional):**
   ```
   CLARITY_API_BASE=https://www.clarity.ms/export-data/api/v1
   ```

---

## Comandos

### Listar projetos configurados
```bash
python3 .claude/skills/int-clarity/scripts/clarity_export.py list-projects
```

### Métricas dos últimos 1-3 dias
```bash
# Visão geral (sem dimensão — retorna todos os metricNames)
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --project ACME --days 1

# Com dimensão: atribuição UTM
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --project ACME --days 3 \
  --dim1 Source --dim2 Medium --dim3 Campaign

# Fricção por URL (Rage Clicks + Dead Clicks)
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --project GLOBEX --days 2 \
  --dim1 URL

# Device breakdown
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --project INITECH --days 1 \
  --dim1 Device --dim2 Browser

# Saída JSON bruta
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --project ACME --days 1 --json

# Varrer todos os projetos (atenção: consome 1 req/projeto da cota de 10/dia)
python3 .claude/skills/int-clarity/scripts/clarity_export.py live-insights --days 1 --all
```

### Health check (smoke)
```bash
# Testa env + chamada real — sempre exit 0, sempre JSON
python3 .claude/skills/int-clarity/scripts/clarity_export.py smoke

# Smoke para projeto específico
python3 .claude/skills/int-clarity/scripts/clarity_export.py smoke --project ACME
```

Exemplo de saída PASS:
```json
{
  "overall": "PASS",
  "steps": [
    {"step": "env_present", "status": "PASS", "tokens_found": 5, "duration_ms": 0},
    {"step": "live_call", "status": "PASS", "project": "ACME", "metrics_returned": 16, "total_rows": 48, "duration_ms": 312}
  ],
  "duration_ms": 313
}
```

---

## Dimensões disponíveis

`Browser` · `Device` · `Country` · `OS` · `Source` · `Medium` · `Campaign` · `Channel` · `URL`

> **Nota:** `Popular Pages` NÃO é uma dimensão válida — retorna body vazio (bug conhecido da Microsoft). Use `URL` para segmentar por página.

## Métricas retornadas

`Traffic` · `Engagement Time` · `Scroll Depth` · `Popular Pages` · `Browser` · `Device` · `OS` · `Country/Region` · `Page Title` · `Referrer URL` · `Dead Click Count` · `Excessive Scroll` · `Rage Click Count` · `Quickback Click` · `Script Error Count` · `Error Click Count`

> **Nota sobre Traffic:** A documentação oficial da Microsoft lista o campo `distantUserCount` (typo na doc). A API real devolve `distinctUserCount` (com "distinct"). O cliente não normaliza nomes de campos — o que a API devolver é preservado tal como veio, sem renomear.

---

## Casos de uso

### Atribuição UTM
Entenda quais canais trazem sessões reais com engajamento:
```bash
live-insights --project <p> --days 3 --dim1 Source --dim2 Medium --dim3 Campaign
```

### Diagnóstico de fricção (UX)
Identifique páginas com alta taxa de Rage Click + Dead Click:
```bash
live-insights --project <p> --days 2 --dim1 URL
```
Combine com `Scroll Depth` por `URL` para detectar abandono.

### Separação bot vs humano
O campo `totalBotSessionCount` em Traffic indica sessões de bot filtradas. `distantUserCount` (typo da Microsoft, preservado) = usuários únicos aproximados.

---

## Limitações

| Limite | Valor |
|--------|-------|
| Janela máxima | 3 dias (numOfDays ∈ {1, 2, 3}) |
| Requisições por dia | 10 por projeto |
| Linhas por resposta | 1.000 (sem paginação) |
| Histórico | Nenhum — apenas últimas 24-72h |
| Fuso horário | UTC |
| Dimensões simultâneas | Máximo 3 (dim1, dim2, dim3) |

> Tokens são por-projeto e têm longa duração (sem TTL documentado pela Microsoft).

---

## Erros comuns

| Código | Causa | Ação |
|--------|-------|------|
| 400 | Parâmetro inválido (days fora de {1,2,3}, dimensão inválida) | O script valida antes da chamada |
| 401 | Token inválido ou expirado | Regenere em Settings → Export Data |
| 403 | Sem permissão de Export Data no projeto | Ative nas configurações do projeto |
| 429 | Cota diária esgotada (10 req/dia) | Aguardar reset 00:00 UTC |
