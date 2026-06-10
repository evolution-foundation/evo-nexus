---
name: int-autentique
description: "Integração com Autentique (assinatura eletrônica BR) via GraphQL API v2. Use para criar documento para assinatura, listar documentos, consultar status (visualizado/assinado/rejeitado), baixar PDF assinado, deletar documentos. Aciona quando o usuário menciona Autentique, assinatura digital, distrato, contrato pra assinar, signatário, envelope. Auth via Bearer token (env ANYCHAT… digo, AUTENTIQUE_API_TOKEN). Suporta entrega por e-mail, WhatsApp, SMS e link curto. Endpoint único: https://api.autentique.com.br/v2/graphql."
homepage: https://www.autentique.com.br
metadata:
  clawdbot:
    emoji: "✍️"
    requires:
      bins: ["python3"]
      env: ["AUTENTIQUE_API_TOKEN"]
    primaryEnv: AUTENTIQUE_API_TOKEN
    files:
      - "scripts/*"
---

# Autentique — assinatura eletrônica brasileira

Cliente Python para a API GraphQL v2 da Autentique. Permite enviar documentos para assinatura digital com validade jurídica MP 2.200-2/2001, gerenciar signatários (e-mail, WhatsApp, SMS), e consultar status. Auth via Bearer token.

> **Quando usar:** distrato, contrato, NDA, proposta comercial, qualquer documento que precise assinatura formal — usar Autentique no lugar de PDF + assinatura digitalizada.

## Setup

1. Gerar token em [https://painel.autentique.com.br/perfil/api](https://painel.autentique.com.br/perfil/api) (já temos conta)
2. Adicionar no `.env`:

```env
AUTENTIQUE_API_TOKEN=seu-token-aqui
```

3. (Opcional) Definir organização padrão se a conta tem múltiplas:

```env
AUTENTIQUE_ORGANIZATION_ID=
```

## CLI

```bash
python3 .claude/skills/int-autentique/scripts/autentique_client.py <command> [args]
```

### Comandos principais

```bash
# Whoami — verifica auth + retorna user/org
autentique_client.py whoami

# Listar documentos (paginado)
autentique_client.py documents list --page 1 --limit 20

# Detalhes de 1 documento (incluindo status de cada signatário)
autentique_client.py documents get DOCUMENT_ID

# Criar documento e enviar pra assinatura
autentique_client.py documents create \
  --file caminho/para/contrato.pdf \
  --name "Termo de Distrato — Cliente X" \
  --signers '[{"email":"cliente@email.com","action":"SIGN"},{"email":"responsavel@empresa.com","action":"SIGN"}]' \
  --message "Segue o termo para assinatura. Qualquer dúvida, retorno por aqui." \
  --reminder WEEKLY

# Sandbox (não consome créditos — para teste)
autentique_client.py documents create --file ... --sandbox

# Baixar PDF original ou assinado
autentique_client.py documents download DOCUMENT_ID --version original --output original.pdf
autentique_client.py documents download DOCUMENT_ID --version signed --output assinado.pdf

# Deletar documento
autentique_client.py documents delete DOCUMENT_ID --confirm

# Listar pastas
autentique_client.py folders list
```

## Tipos de signatário

JSON do `--signers` aceita objetos com:

| Campo | Valores | Notas |
|---|---|---|
| `email` | string | Padrão — Autentique envia link por e-mail |
| `phone` + `delivery_method: DELIVERY_METHOD_WHATSAPP` | E.164 (`+5511...`) | Entrega via WhatsApp |
| `phone` + `delivery_method: DELIVERY_METHOD_SMS` | E.164 | Entrega via SMS (~$0.03) |
| `name` (sem email/phone) | string | Gera link curto pra compartilhar manualmente |
| `action` | `SIGN`, `APPROVE`, `RECOGNIZE`, `SIGN_AS_A_WITNESS` | Default: `SIGN` |
| `configs.cpf` | "12345678900" | Validação por CPF |

## Custos por ação (USD — referência)

| Ação | Custo |
|---|---|
| Criar documento | $0.01 |
| Assinatura via e-mail | $0.002 |
| Assinatura via WhatsApp | $0.02 |
| Assinatura via SMS | $0.03 |
| Consulta de documento | $0.0002 |

**Plano Free:** 20 documentos/mês, 10 req/min. Verificar o plano atual da conta.

## Output

JSON pra stdout em todos os comandos. Em erro: `{"error": ..., "details": ...}` + exit code:
- `0` — sucesso
- `1` — erro de uso (env ausente, JSON inválido, arquivo não encontrado)
- `2` — erro de API (HTTP 4xx/5xx ou erro GraphQL)

## Integrações típicas no workspace

- **Distrato / termo de encerramento:** após aprovação interna do termo, a skill envia pra Autentique → o cliente recebe e assina → status acompanhado via polling do documento
- **Proposta comercial:** gerar a proposta, aprovar internamente e subir pra assinatura
- **NDA:** gerar o NDA e disparar via Autentique para o fornecedor
- **Anexo de documentos no offboarding:** documento separado mas vinculado, assinado junto com o termo principal

## Notas técnicas

- Endpoint: `POST https://api.autentique.com.br/v2/graphql`
- Auth header: `Authorization: Bearer {TOKEN}`
- `createDocument` usa multipart/form-data (specs do GraphQL multipart)
- Rate limit: 10 req/min (Free), 60 (Professional), 200 (Corporate)
- Sandbox: passar `sandbox: true` no input do documento — não consome crédito
- Validade jurídica: MP 2.200-2/2001 + Lei 14.063/2020 (assinatura eletrônica simples)
- Para assinatura **qualificada (ICP-Brasil)**, definir `qualified: true` no documento
