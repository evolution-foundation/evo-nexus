# Postiz no Portainer / Docker Swarm

Esta instalação expõe o Postiz em `https://post.workflowapi.com.br` e permite
que o Omni Nexus em `https://nexus.workflowapi.com.br` publique somente depois
da aprovação humana do ticket.

## 1. Pré-requisitos na VPS

Confirme no manager do Swarm:

```bash
df -h /
free -h
docker network inspect network_public >/dev/null
docker service inspect postgres_postgres >/dev/null
docker service inspect postgres_postgres \
  --format '{{json .Spec.TaskTemplate.Networks}}'
```

O último comando precisa mostrar a mesma rede overlay externa usada pelo
Traefik, normalmente `network_public`. Se o PostgreSQL não estiver nessa rede:

```bash
docker service update --network-add network_public postgres_postgres
```

Crie também um registro DNS `A`:

```text
post.workflowapi.com.br -> IPv4 da VPS
```

## 2. Criar o banco do Postiz

O PostgreSQL `postgres:14` existente serve para o Postiz. Não use o `pgvector`:
o Postiz usa Prisma/PostgreSQL comum e não exige a extensão vector. A stack
inclui o serviço one-shot `postiz-db-bootstrap`, que usa a senha administrativa
do PostgreSQL compartilhado para criar:

```text
role: postiz_user
database: postiz_db_local
owner: postiz_user
```

Portanto, ao importar o `postiz-vps.env` preenchido, não é necessário executar
SQL manualmente. O bootstrap é idempotente e pode rodar novamente em updates.

Como alternativa manual, no repositório do Omni Nexus, no manager:

No repositório do Omni Nexus, no manager:

```bash
export POSTIZ_DB_PASSWORD="$(openssl rand -hex 24)"
export POSTGRES_ADMIN_PASSWORD='<senha do role postgres existente>'
sudo -E bash scripts/bootstrap_postiz_database.sh
```

Guarde o valor de `POSTIZ_DB_PASSWORD`; ele precisa ser exatamente o mesmo no
Portainer. O script cria apenas `postiz_user` e `postiz_db_local`. O Temporal
continua com PostgreSQL dedicado para evitar misturar lifecycle e migrations.

Se o serviço ou usuário administrativo tiver outro nome:

```bash
POSTGRES_SERVICE_NAME=postgres_postgres \
POSTGRES_ADMIN_USER=postgres \
POSTIZ_DB_PASSWORD="$POSTIZ_DB_PASSWORD" \
sudo -E bash scripts/bootstrap_postiz_database.sh
```

## 3. Criar a stack no Portainer

1. Abra **Stacks → Add stack**.
2. Nome sugerido: `postiz`.
3. Use **Web editor** ou envie `postiz-vps.stack.yml`.
4. Cadastre estas variáveis na seção **Environment variables**:

| Variável | Valor |
|---|---|
| `POSTIZ_DOMAIN` | `post.workflowapi.com.br` |
| `POSTIZ_DATABASE_HOST` | `postgres_postgres` |
| `POSTGRES_ADMIN_PASSWORD` | senha real do role `postgres` compartilhado |
| `POSTIZ_DB_PASSWORD` | valor criado no passo 2 |
| `POSTIZ_JWT_SECRET` | saída de `openssl rand -base64 48` |
| `TEMPORAL_DB_PASSWORD` | saída de `openssl rand -hex 24` |
| `POSTIZ_DISABLE_REGISTRATION` | `false` no primeiro acesso |
| `FACEBOOK_APP_ID` | App ID da Meta para Instagram/Facebook |
| `FACEBOOK_APP_SECRET` | App Secret da Meta |
| `LINKEDIN_CLIENT_ID` | Client ID do app LinkedIn |
| `LINKEDIN_CLIENT_SECRET` | Client Secret do app LinkedIn |

Depois clique em **Deploy the stack**.

Neste workspace também pode existir `postiz-vps.env`, já populado com segredos
locais e credenciais sociais reutilizadas do Omni Nexus. Esse arquivo é
gitignored e tem permissão `0600`; cole-o no **Advanced mode** do Portainer em
vez de usar o `.example`. O `.example` nunca contém segredos reais.

O stack cria:

- bootstrap one-shot do banco compartilhado;
- Postiz;
- Redis dedicado;
- PostgreSQL dedicado do Temporal;
- Temporal Server;
- Elasticsearch de visibility;
- Temporal UI apenas na rede interna, sem rota pública.

## 4. Primeiro acesso e OAuth

No primeiro start, a imagem oficial executa `pnpm run prisma-db-push` antes de
subir os processos. Esse comando aplica o schema Prisma inicial no banco vazio;
não existe uma migration SQL separada para você executar. Em upgrades, o mesmo
startup sincroniza o schema novamente.

O botão Google reutiliza `YOUTUBE_CLIENT_ID` e `YOUTUBE_CLIENT_SECRET`. No
Google Cloud Console, adicione:

```text
Authorized JavaScript origin:
https://post.workflowapi.com.br

Authorized redirect URI:
https://post.workflowapi.com.br/integrations/social/youtube
```

SMTP no Postiz usa `EMAIL_PROVIDER=nodemailer` e as variáveis `EMAIL_*`. As
variáveis `N8N_SMTP_*` não são lidas pelo Postiz. Com SMTP habilitado, novas
contas locais precisam abrir o link de ativação recebido por e-mail.

Se `/auth` abrir, mas `/api/auth/can-register` retornar Cloudflare 502, não é
falha de SMTP: o Next.js/frontend está vivo, porém o backend NestJS na porta
interna 3000 não iniciou. Verifique, nesta ordem:

```bash
docker service ps postiz_postiz --no-trunc
docker service logs --tail 300 postiz_postiz
docker service logs --tail 200 postiz_temporal
docker service logs --tail 200 postiz_temporal-postgresql
docker service logs --tail 200 postiz_temporal-elasticsearch
```

Erros contendo `P1001`, `P1000` ou `prisma` apontam para banco/credenciais.
Erros contendo `ECONNREFUSED temporal:7233`, `Name resolution failed for target
dns:temporal:7233` ou `failed to lookup address information` apontam para o DNS
interno/serviço do Temporal. O SMTP e o botão Google só entram depois que
`/api/auth/can-register` já está respondendo.

A stack declara aliases explícitos para `temporal`, `temporal-postgresql`,
`temporal-elasticsearch` e `postiz-redis` na rede overlay interna. Depois de
alterar essa parte, não use apenas **Update the stack** com uma definição antiga:
substitua integralmente o conteúdo pelo `postiz-vps.stack.yml` atual e faça o
redeploy. Em seguida, force a recriação das tasks:

```bash
docker service update \
  --health-cmd 'timeout 5 bash -c "</dev/tcp/$(hostname)/7233"' \
  --health-interval 15s \
  --health-timeout 6s \
  --health-retries 10 \
  --health-start-period 120s \
  postiz_temporal
docker service ps postiz_temporal --no-trunc
```

O `auto-setup` vincula a porta `7233` ao IP da própria task, não ao endereço
`127.0.0.1`. Por isso, o healthcheck usa `$(hostname)`, que resolve para o IP
atual do container. O primeiro comando acima também substitui um healthcheck
antigo diretamente no serviço, inicia uma nova atualização e retoma um update
que tenha sido pausado; não execute outro `--force` logo depois.

Não force PostgreSQL, Elasticsearch e Temporal ao mesmo tempo. Reiniciar as
dependências enquanto o Temporal está conectado provoca perda temporária do
banco, desligamento dos shards e erros como `Not enough hosts to serve the
request`. Após o redeploy da stack, aguarde `postiz_temporal-postgresql` e
`postiz_temporal-elasticsearch` ficarem estáveis; então force somente o
Temporal e espere a task permanecer em `Running` e saudável.

Quando o Temporal estiver estável, confirme o alias pela task atual do Postiz e
só então recrie o Postiz:

```bash
CID=$(docker ps \
  --filter label=com.docker.swarm.service.name=postiz_postiz \
  -q | head -1)

docker exec "$CID" getent hosts temporal
docker exec "$CID" getent hosts temporal-postgresql
docker exec "$CID" getent hosts temporal-elasticsearch
docker service update --force postiz_postiz
```

Os três comandos precisam retornar endereços da rede overlay. Se `temporal`
ainda não resolver, confira se Postiz e Temporal estão conectados à mesma rede:

```bash
docker network inspect postiz_postiz_internal
docker service inspect postiz_postiz \
  --format '{{json .Spec.TaskTemplate.Networks}}'
docker service inspect postiz_temporal \
  --format '{{json .Spec.TaskTemplate.Networks}}'
```

Abra `https://post.workflowapi.com.br`, crie a conta administrativa e conecte
Instagram e LinkedIn.

Cadastre nos provedores OAuth os callbacks exibidos pela própria UI do Postiz.
Como regra, todos devem usar o domínio público HTTPS do Postiz, nunca
`localhost`, IP da VPS ou `nexus.workflowapi.com.br`.

Callbacks usados pela versão atual do Postiz:

```text
https://post.workflowapi.com.br/integrations/social/instagram
https://post.workflowapi.com.br/integrations/social/facebook
https://post.workflowapi.com.br/integrations/social/linkedin
```

Depois do primeiro usuário, altere no Portainer:

```text
POSTIZ_DISABLE_REGISTRATION=true
```

e atualize a stack.

## 5. Criar a API key e localizar integrações

No Postiz, abra **Settings → Public API**, gere/rotacione a API key e teste:

```bash
export POSTIZ_URL=https://post.workflowapi.com.br
export POSTIZ_API_KEY='SUA_CHAVE'

curl -fsS \
  -H "Authorization: $POSTIZ_API_KEY" \
  "$POSTIZ_URL/public/v1/integrations"
```

Copie os `id` das integrações cujo `identifier` seja `instagram` e `linkedin`.

## 6. Conectar o Omni Nexus ao Postiz

Na stack que executa o fork Omni Nexus, configure no serviço
`evonexus_dashboard`:

```text
POSTIZ_URL=https://post.workflowapi.com.br
POSTIZ_API_KEY=<API key do Postiz>
POSTIZ_INTEGRATION_INSTAGRAM_ID=<id retornado pela API>
POSTIZ_INTEGRATION_LINKEDIN_ID=<id retornado pela API>
POSTIZ_ALLOWED_MEDIA_HOSTS=post.workflowapi.com.br,<seu-cdn-ou-storage>
POSTIZ_PUBLISH_TIMEOUT_SECONDS=90
POSTIZ_PUBLISH_POLL_SECONDS=3
```

No serviço `evonexus_telegram`, configure:

```text
APPROVAL_DECISION_TIMEOUT_SECONDS=105
```

`POSTIZ_API_KEY` deve existir somente no dashboard. O código remove essa chave
do ambiente dos subprocessos dos agentes.

## 7. Teste end-to-end

1. Crie um ticket atribuído a um agente publicador.
2. O outcome deve conter `publish_intent=true`, `publish_target`,
   `publish_content` e, para Instagram, `publish_media`.
3. Aprove pelo Telegram.
4. O dashboard envia `POST /public/v1/posts` ao Postiz.
5. O ticket só vira `resolved` quando `GET /public/v1/posts` confirmar
   `state=PUBLISHED` para o `postId` criado.

Se o Postiz responder `QUEUE`, `ERROR` ou não confirmar no timeout, o ticket
volta para `in_progress`; ele nunca é marcado como publicado por suposição.

## Diagnóstico rápido

```bash
docker service ls | grep -E 'postiz|temporal'
docker service ps postiz_postiz --no-trunc
docker service logs -f --tail 200 postiz_postiz
docker service logs -f --tail 200 postiz_temporal
docker service logs -f --tail 200 postiz_temporal-elasticsearch
```

Se o Postiz não alcançar `postgres_postgres`, confira se ambos os serviços
estão conectados à `network_public` e se o DNS interno do Swarm resolve:

```bash
docker run --rm --network network_public postgres:16-alpine \
  pg_isready -h postgres_postgres -p 5432
```
