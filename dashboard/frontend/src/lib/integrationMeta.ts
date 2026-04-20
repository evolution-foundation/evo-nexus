export interface IntegrationFieldMeta {
  envKey: string
  label: string
  hint?: string
  required?: boolean
}

export interface IntegrationMeta {
  id: string
  description: string
  docsUrl?: string
  fields: IntegrationFieldMeta[]
  oauthFlow?: boolean
}

const INTEGRATION_META: IntegrationMeta[] = [
  {
    id: 'stripe',
    description: 'Pagamentos, assinaturas e MRR',
    docsUrl: 'https://stripe.com/docs/api',
    fields: [
      { envKey: 'STRIPE_SECRET_KEY', label: 'Secret Key', hint: 'Usado para cobranças, assinaturas e relatórios de MRR', required: true },
      { envKey: 'STRIPE_WEBHOOK_SECRET', label: 'Webhook Secret', hint: 'Necessário para verificar eventos recebidos', required: false },
    ],
  },
  {
    id: 'omie',
    description: 'ERP brasileiro — clientes, NF-e e financeiro',
    docsUrl: 'https://developer.omie.com.br/',
    fields: [
      { envKey: 'OMIE_APP_KEY', label: 'App Key', hint: 'Chave de acesso do aplicativo Omie', required: true },
      { envKey: 'OMIE_APP_SECRET', label: 'App Secret', hint: 'Segredo de acesso do aplicativo Omie', required: true },
    ],
  },
  {
    id: 'todoist',
    description: 'Gerenciamento de tarefas',
    docsUrl: 'https://developer.todoist.com/',
    fields: [
      { envKey: 'TODOIST_API_TOKEN', label: 'API Token', hint: 'Token de acesso pessoal do Todoist', required: true },
    ],
  },
  {
    id: 'fathom',
    description: 'Reuniões, transcrições e action items',
    docsUrl: 'https://fathom.video/',
    fields: [
      { envKey: 'FATHOM_API_KEY', label: 'API Key', hint: 'Chave de API do Fathom', required: true },
    ],
  },
  {
    id: 'discord',
    description: 'Comunidade — canais, mensagens e moderação',
    docsUrl: 'https://discord.com/developers/docs',
    fields: [
      { envKey: 'DISCORD_BOT_TOKEN', label: 'Bot Token', hint: 'Token do bot Discord', required: true },
      { envKey: 'DISCORD_GUILD_ID', label: 'Guild ID', hint: 'ID do servidor Discord (opcional)', required: false },
    ],
  },
  {
    id: 'telegram',
    description: 'Mensagens, notificações e comandos',
    docsUrl: 'https://core.telegram.org/bots/api',
    fields: [
      { envKey: 'TELEGRAM_BOT_TOKEN', label: 'Bot Token', hint: 'Token do bot Telegram via BotFather', required: true },
      { envKey: 'TELEGRAM_CHAT_ID', label: 'Chat ID', hint: 'ID do chat/grupo destino (opcional, usado como default)', required: false },
    ],
  },
  {
    id: 'whatsapp',
    description: 'Grupos, mensagens e estatísticas via Evolution',
    fields: [
      { envKey: 'WHATSAPP_API_KEY', label: 'API Key', hint: 'Chave de autenticação da API WhatsApp', required: true },
      { envKey: 'WHATSAPP_BASE_URL', label: 'Base URL', hint: 'URL base da instância WhatsApp', required: true },
    ],
  },
  {
    id: 'licensing',
    description: 'Telemetria de instâncias open source',
    fields: [
      { envKey: 'LICENSING_ADMIN_TOKEN', label: 'Admin Token', hint: 'Token de administração do serviço de licenciamento', required: true },
      { envKey: 'LICENSING_API_URL', label: 'API URL', hint: 'URL da API de licenciamento', required: true },
    ],
  },
  {
    id: 'evolution api',
    description: 'API principal Evolution (open source)',
    fields: [
      { envKey: 'EVOLUTION_API_KEY', label: 'API Key', hint: 'Chave de autenticação da Evolution API', required: true },
      { envKey: 'EVOLUTION_API_URL', label: 'API URL', hint: 'URL base da instância Evolution API', required: true },
    ],
  },
  {
    id: 'evolution go',
    description: 'Canal WhatsApp via Evolution Go',
    fields: [
      { envKey: 'EVOLUTION_GO_KEY', label: 'API Key', hint: 'Chave de acesso do Evolution Go', required: true },
      { envKey: 'EVOLUTION_GO_URL', label: 'API URL', hint: 'URL base do Evolution Go', required: true },
    ],
  },
  {
    id: 'evo crm',
    description: 'CRM + agentes de IA (produto principal)',
    fields: [
      { envKey: 'EVO_CRM_TOKEN', label: 'Token', hint: 'Token de acesso do Evo CRM', required: true },
      { envKey: 'EVO_CRM_URL', label: 'URL', hint: 'URL base da instância Evo CRM', required: true },
    ],
  },
  {
    id: 'youtube',
    description: 'Analytics de canal via OAuth',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'instagram',
    description: 'Analytics de perfil via OAuth',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'linkedin',
    description: 'Analytics de perfil/org via OAuth',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'bling',
    description: 'ERP brasileiro — produtos, pedidos, NF-e, contatos',
    docsUrl: 'https://developer.bling.com.br/',
    fields: [
      { envKey: 'BLING_CLIENT_ID', label: 'Client ID', hint: 'Client ID do app Bling (OAuth2)', required: true },
      { envKey: 'BLING_CLIENT_SECRET', label: 'Client Secret', hint: 'Client Secret do app Bling (OAuth2)', required: true },
    ],
  },
  {
    id: 'asaas',
    description: 'Pagamentos brasileiros — Pix, boleto, cartão',
    docsUrl: 'https://asaasv3.docs.apiary.io/',
    fields: [
      { envKey: 'ASAAS_API_KEY', label: 'API Key', hint: 'Chave de API do Asaas', required: true },
    ],
  },
  {
    id: 'github',
    description: 'PRs, issues e releases (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'linear',
    description: 'Issues e projetos de desenvolvimento (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'google calendar',
    description: 'Criar/ler/atualizar eventos (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'gmail',
    description: 'Ler, rascunhar e enviar emails (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'notion',
    description: 'Base de conhecimento (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'canva',
    description: 'Criar e editar designs (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'figma',
    description: 'Arquivos de design e protótipos (via MCP)',
    oauthFlow: true,
    fields: [],
  },
  {
    id: 'ai image creator',
    description: 'Geração de imagens AI — Gemini, FLUX.2, Riverflow, SeedDream, GPT-5',
    docsUrl: '/docs/skills/ai-image-creator',
    fields: [
      { envKey: 'AI_IMG_CREATOR_CF_ACCOUNT_ID', label: 'Cloudflare Account ID', hint: 'Account ID do Cloudflare (Dashboard → Overview)', required: false },
      { envKey: 'AI_IMG_CREATOR_CF_GATEWAY_ID', label: 'Cloudflare Gateway ID', hint: 'Nome do AI Gateway criado no Cloudflare', required: false },
      { envKey: 'AI_IMG_CREATOR_CF_TOKEN', label: 'Cloudflare API Token', hint: 'Token com permissão AI Gateway', required: false },
      { envKey: 'AI_IMG_CREATOR_OPENROUTER_KEY', label: 'OpenRouter API Key', hint: 'Chave sk-or-... do OpenRouter (alternativa ao CF Gateway)', required: false },
      { envKey: 'AI_IMG_CREATOR_GEMINI_KEY', label: 'Google AI Studio Key', hint: 'Chave AI... do Google AI Studio (alternativa)', required: false },
    ],
  },
  // --- LLM providers (used by agents, Knowledge classifier, RAG synthesis) ---
  {
    id: 'openai',
    description: 'GPT models + text-embedding-3 (embeddings Knowledge, opt-in)',
    docsUrl: 'https://platform.openai.com/api-keys',
    fields: [
      { envKey: 'OPENAI_API_KEY', label: 'API Key', hint: 'Chave pessoal da OpenAI — começa com `sk-...`', required: true },
    ],
  },
  {
    id: 'anthropic',
    description: 'Claude Haiku/Sonnet/Opus — classificador automático Knowledge + skills de RAG',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    fields: [
      { envKey: 'ANTHROPIC_API_KEY', label: 'API Key', hint: 'Chave da Anthropic — começa com `sk-ant-...`', required: true },
    ],
  },
  {
    id: 'gemini',
    description: 'Google Gemini — fallback do classificador Knowledge quando Anthropic não está configurada',
    docsUrl: 'https://aistudio.google.com/apikey',
    fields: [
      { envKey: 'GEMINI_API_KEY', label: 'API Key', hint: 'Chave do Google AI Studio', required: true },
    ],
  },
  {
    id: 'voyage ai',
    description: 'Voyage Embeddings (voyage-3-large, 1024 dim) — embedder opt-in v1.1 do Knowledge',
    docsUrl: 'https://docs.voyageai.com/',
    fields: [
      { envKey: 'VOYAGE_API_KEY', label: 'API Key', hint: 'Chave da Voyage AI', required: true },
    ],
  },
  {
    id: 'llamaparse',
    description: 'Parser premium de PDFs visualmente complexos — opt-in do Knowledge',
    docsUrl: 'https://cloud.llamaindex.ai/parse',
    fields: [
      { envKey: 'LLAMAPARSE_API_KEY', label: 'API Key', hint: 'Chave da LlamaIndex Cloud', required: true },
    ],
  },
]

/**
 * Returns metadata for an integration by name.
 * Matching is case-insensitive.
 */
export function getIntegrationMeta(name: string): IntegrationMeta | undefined {
  const normalized = name.toLowerCase().trim()
  return INTEGRATION_META.find((m) => m.id === normalized)
}

export default INTEGRATION_META
