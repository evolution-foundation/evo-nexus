# Como testar localmente no Windows (Docker)

## Pré-requisitos

- Docker Desktop instalado e rodando
- Terminal (PowerShell, cmd, ou Git Bash) em `D:\evo-nexus`

## Passo a passo

### 1. Configurar variáveis de ambiente

```bash
# No diretório D:\evo-nexus
copy .env.example .env
```

Edite o `.env` e coloque no mínimo:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Subir o container

```bash
docker compose -f docker-compose.dev.yml up --build
```

O primeiro build demora ~5-10 minutos (baixa Node.js, Python, instala deps).
Os próximos iniciam em ~30 segundos (usa cache).

### 3. Acessar o dashboard

Abra: http://localhost:8080

Na primeira abertura, o wizard de setup vai aparecer para criar a conta admin.

### 4. Testar o fluxo de onboarding

Após criar a conta, o wizard de onboarding vai aparecer automaticamente.

Para testar o Brain Repo:
1. Crie um PAT no GitHub: https://github.com/settings/tokens/new?scopes=repo
2. Use o PAT na tela `StepBrainConnect`
3. Escolha criar novo repo `evo-brain-<seu-username>`

### 5. Parar o container

```bash
docker compose -f docker-compose.dev.yml down
```

Para também apagar os dados (reset completo):
```bash
docker compose -f docker-compose.dev.yml down -v
```

### 6. Ver logs em tempo real

```bash
docker logs -f evonexus-dev
```

## Portas expostas

| Porta | Serviço |
|-------|---------|
| 8080  | Dashboard (Flask + React) |
| 32352 | Terminal server (Claude Code) |

## Dicas

**Mudanças no backend (Python):** Reinicie o container para pegar as mudanças:
```bash
docker restart evonexus-dev
```

**Mudanças no frontend (React):** Precisa rebuild:
```bash
docker compose -f docker-compose.dev.yml up --build
```

**Acessar o shell do container:**
```bash
docker exec -it evonexus-dev bash
```
