---
name: int-portainer
description: "Integração com Portainer para monitoramento de containers e controle de stacks. Instâncias configuradas via PORTAINER_<NOME>_URL e PORTAINER_<NOME>_TOKEN no .env. Aciona quando o usuário menciona Portainer, containers, stacks, Docker, ou pergunta sobre infraestrutura/serviços rodando."
metadata:
  openclaw:
    requires:
      env:
        - PORTAINER_<NOME>_URL
        - PORTAINER_<NOME>_TOKEN
      bins:
        - python3
    primaryEnv: PORTAINER_<NOME>_TOKEN
---

# Portainer

Integração com Portainer para monitoramento e controle de infraestrutura Docker. Funciona com qualquer número de instâncias — basta configurar pares de variáveis de ambiente no `.env` do repositório.

## Configuração

Cada instância Portainer requer um par de variáveis no `.env`:

```bash
# Formato: PORTAINER_<NOME>_URL e PORTAINER_<NOME>_TOKEN
# <NOME> em maiúsculas; o nome da instância na CLI é em minúsculas.

PORTAINER_PROD_URL=https://portainer.sua-empresa.com.br
PORTAINER_PROD_TOKEN=ptr_xxxxxxxxxxxxxxxxxxxxxxxx

PORTAINER_STAGING_URL=https://portainer-staging.sua-empresa.com.br
PORTAINER_STAGING_TOKEN=ptr_yyyyyyyyyyyyyyyyyyyyyyyy
```

Com essa configuração, as instâncias disponíveis serão `prod`, `staging` e `all`.

## Script

```
.claude/skills/int-portainer/scripts/portainer.py
```

Uso:
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py <instância> <comando> [opções]
```

`instância`: nome da instância (conforme `<NOME>` nas vars de ambiente) | `all`

---

## Comandos

### Healthcheck de conexão
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py prod ping
python3 .claude/skills/int-portainer/scripts/portainer.py all ping
```

### Resumo de saúde (containers + stacks problemáticos)
```bash
# Verificação rápida — mostra só o que tem problema
python3 .claude/skills/int-portainer/scripts/portainer.py prod health
python3 .claude/skills/int-portainer/scripts/portainer.py staging health
python3 .claude/skills/int-portainer/scripts/portainer.py all health
```

### Listar endpoints (ambientes Docker)
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py prod endpoints
python3 .claude/skills/int-portainer/scripts/portainer.py staging endpoints --format json
```

### Listar containers
```bash
# Só containers com problema (padrão — ideal para monitoramento)
python3 .claude/skills/int-portainer/scripts/portainer.py prod containers

# Todos os containers de um endpoint específico
python3 .claude/skills/int-portainer/scripts/portainer.py prod containers --endpoint-id 1 --all

# Formato JSON
python3 .claude/skills/int-portainer/scripts/portainer.py staging containers --all --format json
```

### Listar stacks
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py prod stacks
python3 .claude/skills/int-portainer/scripts/portainer.py staging stacks
python3 .claude/skills/int-portainer/scripts/portainer.py all stacks
```

### Ligar uma stack
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py prod stack-start --stack-id <ID>
python3 .claude/skills/int-portainer/scripts/portainer.py staging stack-start --stack-id <ID>
```

### Desligar uma stack
```bash
python3 .claude/skills/int-portainer/scripts/portainer.py prod stack-stop --stack-id <ID>
python3 .claude/skills/int-portainer/scripts/portainer.py staging stack-stop --stack-id <ID>
```

### Ver logs de um container
```bash
# Por nome do container, últimas 50 linhas (padrão)
python3 .claude/skills/int-portainer/scripts/portainer.py prod logs \
  --endpoint-id 1 --container nome-do-container

# Últimas 100 linhas
python3 .claude/skills/int-portainer/scripts/portainer.py prod logs \
  --endpoint-id 1 --container nome-do-container --lines 100
```

---

## Opções globais

| Flag | Descrição |
|------|-----------|
| `--endpoint-id` / `-e` | ID do endpoint Docker (necessário para containers/logs) |
| `--stack-id` / `-s` | ID da stack (necessário para start/stop) |
| `--container` / `-c` | Nome ou ID do container (necessário para logs) |
| `--lines` / `-n` | Número de linhas de log (padrão: 50) |
| `--all` | Mostra todos os containers, não só problemáticos |
| `--format` / `-f` | `table` (padrão) ou `json` |

---

## Fluxo de monitoramento recomendado

Quando o usuário pedir status da infraestrutura:

1. Rodar `health` em todas as instâncias configuradas (`all`)
2. Se houver containers com problema → rodar `logs` para investigar
3. Se houver stacks paradas → confirmar com o usuário antes de `stack-start`
