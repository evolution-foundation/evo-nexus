#!/usr/bin/env python3
"""
EvoNexus — Setup Wizard
Generates workspace configuration, CLAUDE.md, .env, and folder structure.
Usage: python setup.py  (or: make setup)

This file also doubles as a setuptools build backend when invoked by pip
(e.g. via `pip install -e .` or `npx @evoapi/evo-nexus`). In that case the
interactive wizard is skipped and only package metadata is produced — see
`_IS_BUILD_BACKEND` below.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent

# ── Non-interactive / build-backend detection ──────────────────────────
#
# We use TWO signals (narrow and explicit) to avoid the false-positive
# problem Sourcery flagged on upstream PR #11:
#
#   1. _IS_TTY    — true when stdin is an interactive terminal.
#                   Fixes the EOFError from `input()` under pip/npx.
#
#   2. _IS_BUILD_BACKEND — true ONLY when setuptools/pip is the caller.
#                   Detected via the explicit env var EVO_NEXUS_INSTALL=1
#                   (set by cli/bin/cli.mjs) or narrow argv markers that
#                   setuptools always injects (egg_info / dist_info /
#                   bdist_wheel / --editable). We deliberately do NOT use
#                   generic args like --version because a direct call
#                   `python setup.py --version` should remain interactive-ish.
#
_IS_TTY = sys.stdin.isatty() if sys.stdin else False

_BUILD_ARGV_MARKERS = {"egg_info", "dist_info", "bdist_wheel", "sdist", "--editable"}
_IS_BUILD_BACKEND = (
    os.environ.get("EVO_NEXUS_INSTALL") == "1"
    or any(a in _BUILD_ARGV_MARKERS for a in sys.argv[1:])
)


def _read_version_from_pyproject() -> str:
    """Single source of truth for the package version.

    Reads [project].version from pyproject.toml. Avoids the drift risk
    Sourcery flagged on PR #11 (hardcoded "0.23.2" string).
    """
    try:
        import re
        pyproject = (WORKSPACE / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        if match:
            return match.group(1)
    except (OSError, ImportError):
        pass
    return "0.0.0"  # fallback; never expected in practice


# ANSI colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ─────────────────────────────────────────────────────────────────────────────
# Setup wizard i18n (pt-BR / en-US / es)
# ─────────────────────────────────────────────────────────────────────────────
# Matches the BCP-47 tags the dashboard UI uses. The selected language also
# becomes the default `workspace.language` saved in config/workspace.yaml, so
# what the user picks here is what the dashboard later renders in.

LANG = "en-US"  # mutated by select_language() before main() runs

MESSAGES = {
    "en-US": {
        "choose_lang_prompt": "Choose your language / Escolha seu idioma / Elige tu idioma",
        "choose_lang_option_1": "English (US)",
        "choose_lang_option_2": "Português (BR)",
        "choose_lang_option_3": "Español",
        "choose_lang_ask": "Type 1, 2 or 3",
        "banner_title": "EvoNexus — Setup Wizard",
        "checking_prereqs": "Checking prerequisites...",
        "dashboard_access": "Dashboard Access",
        "quick_remote_setup": "Quick setup for remote access...",
        "ai_provider": "AI Provider",
        "about_you": "About you",
        "your_name": "Your name",
        "company_name": "Company name",
        "timezone": "Timezone",
        "language": "Language",
        "dashboard_port": "Dashboard port",
        "creating_workspace": "Creating workspace...",
        "installing_python_deps": "Installing Python dependencies...",
        "installed_python_deps": "Installed Python dependencies",
        "python_deps_failed": "Python dependencies failed to install",
        "python_deps_needed": "This is needed for the dashboard to work.",
        "try_manually": "Try running manually:",
        "log_at": "Log:",
        "installing_dashboard_deps": "Installing dashboard dependencies...",
        "installed_dashboard_deps": "Installed dashboard dependencies",
        "dashboard_deps_failed": "Dashboard dependencies failed",
        "building_dashboard": "Building dashboard frontend...",
        "built_dashboard": "Built dashboard frontend",
        "dashboard_build_failed": "Dashboard build failed",
        "installing_terminal_deps": "Installing terminal-server dependencies...",
        "installed_terminal_deps": "Installed terminal-server dependencies",
        "terminal_deps_failed": "Terminal-server dependencies failed",
        "services_user_note": "(services will run as {user})",
        "terminal_started": "Terminal server started (port 32352)",
        "terminal_not_started": "Terminal server may not have started — check logs/terminal-server.log",
        "dashboard_started": "Dashboard started (port 8080)",
        "dashboard_not_started": "Dashboard may not have started — check logs/dashboard.log",
        "setup_done": "Setup complete!",
        "dashboard_available_at": "Dashboard available at:",
        "next_steps_header": "Next steps:",
        "next_step_1_remote": "1. Open the link above and create your admin account",
        "next_step_2_remote": "2. Go to {bold}Providers{reset} and configure your AI Provider",
        "next_step_3_remote": "3. Open an agent and start using it!",
        "next_step_1_local": "1. Edit {bold}.env{reset} with your API keys",
        "next_step_2_local": "2. Run: {bold}make dashboard-app{reset}",
        "next_step_3_local": "3. Open {bold}{url}{reset} to create your admin account",
        "systemd_section": "systemd service:",
        "systemd_status": "check status",
        "systemd_restart": "restart",
        "systemd_logs": "view logs",
        "systemd_su": "switch to service user",
        # Prerequisites + tools
        "sys_packages_updating": "Updating system packages...",
        "sys_packages_updated": "System packages updated",
        "tool_installed": "installed",
        # Dashboard access wizard
        "local_only_option": "Local only (http://localhost:8080)",
        "domain_ssl_option": "Domain with SSL (recommended for remote servers)",
        "type_1_or_2": "Type 1 or 2",
        "domain_prompt": "Domain (e.g. nexus.example.com)",
        "installing_nginx": "Installing nginx...",
        "nginx_installed": "nginx installed",
        "nginx_install_failed": "nginx installation failed, using local mode",
        "ssl_cert_prompt": "SSL certificate (1=certbot, 2=self-signed, 3=manual path)",
        "cert_existing_found": "Existing certbot certificate found for {domain}",
        "installing_certbot": "Installing certbot...",
        "certbot_installed": "certbot installed",
        "obtaining_ssl_certbot": "Obtaining SSL certificate via certbot...",
        "ssl_obtained_certbot": "SSL certificate obtained via certbot",
        "certbot_failed_fallback": "certbot failed — falling back to self-signed",
        "generating_self_signed": "Generating self-signed SSL certificate...",
        "self_signed_generated": "Self-signed SSL certificate generated",
        "self_signed_cloudflare_note": "(Compatible with Cloudflare SSL mode: Full)",
        "self_signed_failed": "Failed to generate SSL certificate",
        "no_ssl_cert_local_mode": "No SSL certificate available, using local mode",
        "manual_cert_prompt": "Path to certificate (.crt or .pem)",
        "manual_key_prompt": "Path to private key (.key)",
        "nginx_configured_for": "Nginx configured for {domain}",
        "nginx_config_failed": "Failed to configure nginx",
        "configuring_firewall": "Configuring firewall...",
        "firewall_ports_opened": "Firewall ports opened (80, 443)",
        "firewall_using_ufw": "Using ufw",
        "firewall_using_iptables": "Using iptables (ufw not installed)",
        "firewall_persisted": "Rules persisted via {tool} (will survive reboot)",
        "firewall_persistence_missing": "Rules opened in memory only — install netfilter-persistent OR ufw to persist across reboots",
        "firewall_install_persistence": "Installing netfilter-persistent so rules survive reboot...",
        "firewall_failed": "Firewall step failed: {err}",
        "firewall_cloud_provider_hint": "Cloud provider firewall: if 80/443 still appear blocked from outside, also open them in your provider's Security List/Group ({provider}).",
        # Workspace file creation
        "generated_workspace_yaml": "Generated config/workspace.yaml",
        "env_created_from_example": "Created .env from .env.example",
        "env_example_missing": ".env.example not found, creating empty .env",
        "env_already_exists": ".env already exists, skipping",
        "generated_master_key": "Generated KNOWLEDGE_MASTER_KEY (Knowledge Base encryption)",
        "master_key_already_set": "KNOWLEDGE_MASTER_KEY already set — preserved",
        "master_key_skip_crypto_missing": "Skipped KNOWLEDGE_MASTER_KEY generation ({exc})",
        "master_key_run_init_hint": "Run `make init-key` after setup completes to generate it.",
        "master_key_ensure_failed": "Could not ensure KNOWLEDGE_MASTER_KEY: {exc}",
        "generated_routines_yaml": "Created config/routines.yaml",
        "routines_already_exists": "config/routines.yaml already exists, skipping",
        "generated_claude_md": "Generated CLAUDE.md",
        "created_workspace_folders": "Created workspace folders ({count})",
        # Systemd / service lifecycle
        "fixing_ownership": "Fixing file ownership for {user}...",
        "ownership_fixed": "Ownership fixed",
        "starting_dashboard_services": "Starting dashboard services...",
        "creating_systemd_service": "Creating systemd service...",
        "systemd_service_created": "Systemd service created and enabled (auto-starts on boot)",
        "systemd_manage_hint": "Manage with: systemctl {{start|stop|restart|status}} {service}",
        # Prerequisite tool check
        "tool_not_found": "{name} not found",
        "tool_installing_verb": "Installing {name}...",
        "tool_upgrading_verb": "Upgrading {name}...",
        "tool_install_failed": "Failed to install {name}",
        "tool_upgrade_failed": "Failed to upgrade {name}",
        "tool_required": "{name} is required for EvoNexus",
        "tool_install_manually": "{name} not found — install manually",
        "tool_skip_noninteractive": "Skipping auto-install in non-interactive mode.",
        "tool_run_manually": "Run manually: {cmd}",
        "tool_install_prompt": "Install {name}? (Y/n): ",
        "tool_upgrade_hint": "(upgrading to {required}+)",
        "installing_build_essential": "Installing build-essential...",
        "build_essential_failed": "build-essential install failed",
        "npm_not_found": "npm not found (should come with Node.js)",
        "prereq_install_failed_header": "The following tools could not be installed:",
        "prereq_install_manually_retry": "Install them manually and run setup again.",
        "invalid_choice_local_mode": "Invalid choice '{choice}'. Using local mode.",
        "no_domain_local_mode": "No domain provided, using local mode",
        "nginx_config_test_failed": "Nginx config test failed",
        "nginx_config_saved_at": "The config is saved at {path}",
        "nginx_fix_and_reload": "Fix the issue and run: nginx -t && systemctl reload nginx",
        "nginx_config_not_created": "Nginx config file was not created at {path}",
        "nginx_no_permission": "No permission to write nginx config — run setup as root/sudo",
        "removed_nginx_default_site": "Removed nginx default site",
        # Install dir auto-relocation
        "install_inaccessible": "User '{user}' cannot access {path} (likely /root/* with mode 700)",
        "install_relocating": "Relocating install to {dest} so the service user can read it...",
        "install_relocated": "Install relocated to {dest}",
        "install_relocate_failed": "Failed to relocate install — check disk space / permissions",
        "install_relocate_hint": "Original copy at {orig} can be removed after setup completes",
        # AI Provider wizard
        "choose_ai_provider_header": "Choose your AI provider:",
        "provider_opt1_anthropic": "Anthropic (native Claude)",
        "provider_opt1_note": "default, no extra config",
        "provider_opt2_openrouter": "OpenRouter (200+ models)",
        "provider_opt2_note": "requires API key + openclaude",
        "provider_opt3_openai": "OpenAI (GPT-4.x / GPT-5.x)",
        "provider_opt3_note": "API key or OAuth + openclaude",
        "provider_opt4_gemini": "Google Gemini",
        "provider_opt5_bedrock": "AWS Bedrock",
        "provider_opt6_vertex": "Google Vertex AI",
        "provider_coming_soon_label": "coming soon",
        "provider_select_prompt": "Provider (1-3)",
        "provider_coming_soon_fallback": "This provider is coming soon. Using Anthropic for now.",
        "openclaude_not_found_for_provider": "openclaude not found — needed for {provider}",
        "install_now_prompt": "Install now? (y/n)",
        "provider_config_saved": "Saved provider config: {provider}",
        "provider_remember_logout": "Remember to run /logout in Claude Code if previously logged into Anthropic",
        "openai_auth_header": "OpenAI Authentication",
        "openai_auth_opt_a": "API Key (GPT-4.x)",
        "openai_auth_opt_b": "Codex OAuth (GPT-5.x) — via Dashboard",
        "openai_auth_method_prompt": "Auth method (a/b)",
        "openai_provider_configured": "Provider configured: OpenAI (Codex OAuth)",
        "openai_complete_via_dashboard": "To complete authentication, open the Dashboard",
        "openai_dashboard_path": "Providers → Login with OpenAI",
        "configure_provider_header": "Configure {name}",
        "multi_select_hint": "Enter keys to toggle (comma-separated), or press Enter to accept:",
        # Brain Repo (versioning)
        "brain_repo_enable_prompt": "Enable Brain Repo? (version your memory/workspace to GitHub)",
        "brain_repo_auth_method": "Authentication method",
        "brain_repo_defer_to_web": "Configure in web UI later",
        "brain_repo_pat_instructions": "Create a GitHub PAT at https://github.com/settings/tokens/new?scopes=repo",
        "brain_repo_pat_prompt": "GitHub Personal Access Token (scope: repo)",
        "brain_repo_pat_saved": "Brain repo PAT saved. Complete setup in the web UI.",
        "brain_repo_pat_skipped": "Skipped. You can configure Brain Repo in Settings > Integrations.",
        "brain_repo_configure_later": "OK! You can connect GitHub in Settings > Integrations.",
    },
    "pt-BR": {
        "choose_lang_prompt": "Choose your language / Escolha seu idioma / Elige tu idioma",
        "choose_lang_option_1": "English (US)",
        "choose_lang_option_2": "Português (BR)",
        "choose_lang_option_3": "Español",
        "choose_lang_ask": "Digite 1, 2 ou 3",
        "banner_title": "EvoNexus — Assistente de Instalação",
        "checking_prereqs": "Verificando pré-requisitos...",
        "dashboard_access": "Acesso ao Dashboard",
        "quick_remote_setup": "Configuração rápida para acesso remoto...",
        "ai_provider": "Provedor de IA",
        "about_you": "Sobre você",
        "your_name": "Seu nome",
        "company_name": "Nome da empresa",
        "timezone": "Fuso horário",
        "language": "Idioma",
        "dashboard_port": "Porta do dashboard",
        "creating_workspace": "Criando workspace...",
        "installing_python_deps": "Instalando dependências Python...",
        "installed_python_deps": "Dependências Python instaladas",
        "python_deps_failed": "Falha ao instalar dependências Python",
        "python_deps_needed": "Isso é necessário para o dashboard funcionar.",
        "try_manually": "Execute manualmente:",
        "log_at": "Log:",
        "installing_dashboard_deps": "Instalando dependências do dashboard...",
        "installed_dashboard_deps": "Dependências do dashboard instaladas",
        "dashboard_deps_failed": "Falha ao instalar dependências do dashboard",
        "building_dashboard": "Construindo o dashboard...",
        "built_dashboard": "Dashboard construído",
        "dashboard_build_failed": "Falha ao construir o dashboard",
        "installing_terminal_deps": "Instalando dependências do terminal-server...",
        "installed_terminal_deps": "Dependências do terminal-server instaladas",
        "terminal_deps_failed": "Falha ao instalar dependências do terminal-server",
        "services_user_note": "(serviços serão executados como {user})",
        "terminal_started": "Terminal server iniciado (porta 32352)",
        "terminal_not_started": "Terminal server pode não ter iniciado — verifique logs/terminal-server.log",
        "dashboard_started": "Dashboard iniciado (porta 8080)",
        "dashboard_not_started": "Dashboard pode não ter iniciado — verifique logs/dashboard.log",
        "setup_done": "Instalação concluída!",
        "dashboard_available_at": "Dashboard disponível em:",
        "next_steps_header": "Próximos passos:",
        "next_step_1_remote": "1. Acesse o link acima e crie sua conta de administrador",
        "next_step_2_remote": "2. Vá em {bold}Provedores{reset} e configure seu provedor de IA",
        "next_step_3_remote": "3. Abra um agente e comece a usar!",
        "next_step_1_local": "1. Edite {bold}.env{reset} com suas chaves de API",
        "next_step_2_local": "2. Execute: {bold}make dashboard-app{reset}",
        "next_step_3_local": "3. Abra {bold}{url}{reset} e crie sua conta de administrador",
        "systemd_section": "Serviço systemd:",
        "systemd_status": "verificar status",
        "systemd_restart": "reiniciar",
        "systemd_logs": "ver logs",
        "systemd_su": "acessar o usuário do serviço",
        # Prerequisites + tools
        "sys_packages_updating": "Atualizando pacotes do sistema...",
        "sys_packages_updated": "Pacotes do sistema atualizados",
        "tool_installed": "instalado",
        # Dashboard access wizard
        "local_only_option": "Apenas local (http://localhost:8080)",
        "domain_ssl_option": "Domínio com SSL (recomendado para servidores remotos)",
        "type_1_or_2": "Digite 1 ou 2",
        "domain_prompt": "Domínio (ex: nexus.exemplo.com)",
        "installing_nginx": "Instalando nginx...",
        "nginx_installed": "nginx instalado",
        "nginx_install_failed": "Falha ao instalar nginx, usando modo local",
        "ssl_cert_prompt": "Certificado SSL (1=certbot, 2=auto-assinado, 3=caminho manual)",
        "cert_existing_found": "Certificado certbot existente encontrado para {domain}",
        "installing_certbot": "Instalando certbot...",
        "certbot_installed": "certbot instalado",
        "obtaining_ssl_certbot": "Obtendo certificado SSL via certbot...",
        "ssl_obtained_certbot": "Certificado SSL obtido via certbot",
        "certbot_failed_fallback": "certbot falhou — usando auto-assinado",
        "generating_self_signed": "Gerando certificado SSL auto-assinado...",
        "self_signed_generated": "Certificado SSL auto-assinado gerado",
        "self_signed_cloudflare_note": "(Compatível com o modo SSL Full do Cloudflare)",
        "self_signed_failed": "Falha ao gerar certificado SSL",
        "no_ssl_cert_local_mode": "Sem certificado SSL disponível, usando modo local",
        "manual_cert_prompt": "Caminho do certificado (.crt ou .pem)",
        "manual_key_prompt": "Caminho da chave privada (.key)",
        "nginx_configured_for": "Nginx configurado para {domain}",
        "nginx_config_failed": "Falha ao configurar o nginx",
        "configuring_firewall": "Configurando firewall...",
        "firewall_ports_opened": "Portas do firewall abertas (80, 443)",
        "firewall_using_ufw": "Usando ufw",
        "firewall_using_iptables": "Usando iptables (ufw não instalado)",
        "firewall_persisted": "Regras persistidas via {tool} (vão sobreviver ao reboot)",
        "firewall_persistence_missing": "Regras abertas só na memória — instale netfilter-persistent OU ufw para persistir entre reboots",
        "firewall_install_persistence": "Instalando netfilter-persistent para persistir regras no reboot...",
        "firewall_failed": "Etapa do firewall falhou: {err}",
        "firewall_cloud_provider_hint": "Firewall do provedor: se 80/443 ainda aparecerem bloqueados de fora, abra também na Security List/Group do seu provedor ({provider}).",
        # Workspace file creation
        "generated_workspace_yaml": "Gerado config/workspace.yaml",
        "env_created_from_example": "Criado .env a partir do .env.example",
        "env_example_missing": ".env.example não encontrado, criando .env vazio",
        "env_already_exists": ".env já existe, ignorando",
        "generated_master_key": "KNOWLEDGE_MASTER_KEY gerado (criptografia da Knowledge Base)",
        "master_key_already_set": "KNOWLEDGE_MASTER_KEY já definido — preservado",
        "master_key_skip_crypto_missing": "Geração de KNOWLEDGE_MASTER_KEY ignorada ({exc})",
        "master_key_run_init_hint": "Rode `make init-key` após o setup para gerá-lo.",
        "master_key_ensure_failed": "Não foi possível garantir KNOWLEDGE_MASTER_KEY: {exc}",
        "generated_routines_yaml": "Criado config/routines.yaml",
        "routines_already_exists": "config/routines.yaml já existe, ignorando",
        "generated_claude_md": "Gerado CLAUDE.md",
        "created_workspace_folders": "Pastas do workspace criadas ({count})",
        # Systemd / service lifecycle
        "fixing_ownership": "Ajustando permissões de arquivos para {user}...",
        "ownership_fixed": "Permissões ajustadas",
        "starting_dashboard_services": "Iniciando serviços do dashboard...",
        "creating_systemd_service": "Criando serviço systemd...",
        "systemd_service_created": "Serviço systemd criado e habilitado (inicia no boot)",
        "systemd_manage_hint": "Gerencie com: systemctl {{start|stop|restart|status}} {service}",
        # Prerequisite tool check
        "tool_not_found": "{name} não encontrado",
        "tool_installing_verb": "Instalando {name}...",
        "tool_upgrading_verb": "Atualizando {name}...",
        "tool_install_failed": "Falha ao instalar {name}",
        "tool_upgrade_failed": "Falha ao atualizar {name}",
        "tool_required": "{name} é obrigatório para o EvoNexus",
        "tool_install_manually": "{name} não encontrado — instale manualmente",
        "tool_skip_noninteractive": "Pulando instalação automática em modo não-interativo.",
        "tool_run_manually": "Execute manualmente: {cmd}",
        "tool_install_prompt": "Instalar {name}? (S/n): ",
        "tool_upgrade_hint": "(atualizando para {required}+)",
        "installing_build_essential": "Instalando build-essential...",
        "build_essential_failed": "Falha ao instalar build-essential",
        "npm_not_found": "npm não encontrado (deveria vir com o Node.js)",
        "prereq_install_failed_header": "Os seguintes utilitários não puderam ser instalados:",
        "prereq_install_manually_retry": "Instale-os manualmente e execute o setup novamente.",
        "invalid_choice_local_mode": "Opção inválida '{choice}'. Usando modo local.",
        "no_domain_local_mode": "Nenhum domínio informado, usando modo local",
        "nginx_config_test_failed": "Teste de configuração do nginx falhou",
        "nginx_config_saved_at": "A configuração foi salva em {path}",
        "nginx_fix_and_reload": "Corrija o problema e execute: nginx -t && systemctl reload nginx",
        "nginx_config_not_created": "Arquivo de configuração do nginx não foi criado em {path}",
        "nginx_no_permission": "Sem permissão para escrever a configuração do nginx — execute o setup como root/sudo",
        "removed_nginx_default_site": "Site padrão do nginx removido",
        # Install dir auto-relocation
        "install_inaccessible": "Usuário '{user}' não consegue acessar {path} (provavelmente /root/* com modo 700)",
        "install_relocating": "Movendo instalação para {dest} para que o usuário do serviço consiga ler...",
        "install_relocated": "Instalação movida para {dest}",
        "install_relocate_failed": "Falha ao mover a instalação — verifique espaço em disco / permissões",
        "install_relocate_hint": "A cópia original em {orig} pode ser removida após o setup terminar",
        # AI Provider wizard
        "choose_ai_provider_header": "Escolha seu provedor de IA:",
        "provider_opt1_anthropic": "Anthropic (Claude nativo)",
        "provider_opt1_note": "padrão, sem configuração extra",
        "provider_opt2_openrouter": "OpenRouter (200+ modelos)",
        "provider_opt2_note": "requer chave de API + openclaude",
        "provider_opt3_openai": "OpenAI (GPT-4.x / GPT-5.x)",
        "provider_opt3_note": "chave de API ou OAuth + openclaude",
        "provider_opt4_gemini": "Google Gemini",
        "provider_opt5_bedrock": "AWS Bedrock",
        "provider_opt6_vertex": "Google Vertex AI",
        "provider_coming_soon_label": "em breve",
        "provider_select_prompt": "Provedor (1-3)",
        "provider_coming_soon_fallback": "Este provedor estará disponível em breve. Usando Anthropic por enquanto.",
        "openclaude_not_found_for_provider": "openclaude não encontrado — necessário para {provider}",
        "install_now_prompt": "Instalar agora? (s/n)",
        "provider_config_saved": "Configuração de provedor salva: {provider}",
        "provider_remember_logout": "Lembre de rodar /logout no Claude Code se estava logado na Anthropic",
        "openai_auth_header": "Autenticação OpenAI",
        "openai_auth_opt_a": "Chave de API (GPT-4.x)",
        "openai_auth_opt_b": "Codex OAuth (GPT-5.x) — via Dashboard",
        "openai_auth_method_prompt": "Método de autenticação (a/b)",
        "openai_provider_configured": "Provedor configurado: OpenAI (Codex OAuth)",
        "openai_complete_via_dashboard": "Para concluir a autenticação, acesse o Dashboard",
        "openai_dashboard_path": "Provedores → Login com OpenAI",
        "configure_provider_header": "Configurar {name}",
        "multi_select_hint": "Digite as teclas para alternar (separadas por vírgula), ou Enter para aceitar:",
        # Brain Repo (versionamento)
        "brain_repo_enable_prompt": "Ativar Brain Repo? (versione sua memória/workspace no GitHub)",
        "brain_repo_auth_method": "Método de autenticação",
        "brain_repo_defer_to_web": "Configurar pela interface web depois",
        "brain_repo_pat_instructions": "Crie um PAT do GitHub em https://github.com/settings/tokens/new?scopes=repo",
        "brain_repo_pat_prompt": "Token de Acesso Pessoal do GitHub (escopo: repo)",
        "brain_repo_pat_saved": "PAT do Brain Repo salvo. Conclua a configuração pela interface web.",
        "brain_repo_pat_skipped": "Pulado. Configure o Brain Repo em Configurações > Integrações.",
        "brain_repo_configure_later": "Certo! Conecte o GitHub em Configurações > Integrações.",
    },
    "es": {
        "choose_lang_prompt": "Choose your language / Escolha seu idioma / Elige tu idioma",
        "choose_lang_option_1": "English (US)",
        "choose_lang_option_2": "Português (BR)",
        "choose_lang_option_3": "Español",
        "choose_lang_ask": "Escribe 1, 2 o 3",
        "banner_title": "EvoNexus — Asistente de Instalación",
        "checking_prereqs": "Verificando requisitos...",
        "dashboard_access": "Acceso al Dashboard",
        "quick_remote_setup": "Configuración rápida para acceso remoto...",
        "ai_provider": "Proveedor de IA",
        "about_you": "Sobre ti",
        "your_name": "Tu nombre",
        "company_name": "Nombre de la empresa",
        "timezone": "Zona horaria",
        "language": "Idioma",
        "dashboard_port": "Puerto del dashboard",
        "creating_workspace": "Creando workspace...",
        "installing_python_deps": "Instalando dependencias Python...",
        "installed_python_deps": "Dependencias Python instaladas",
        "python_deps_failed": "Error al instalar dependencias Python",
        "python_deps_needed": "Esto es necesario para que el dashboard funcione.",
        "try_manually": "Ejecuta manualmente:",
        "log_at": "Registro:",
        "installing_dashboard_deps": "Instalando dependencias del dashboard...",
        "installed_dashboard_deps": "Dependencias del dashboard instaladas",
        "dashboard_deps_failed": "Error al instalar dependencias del dashboard",
        "building_dashboard": "Construyendo el dashboard...",
        "built_dashboard": "Dashboard construido",
        "dashboard_build_failed": "Error al construir el dashboard",
        "installing_terminal_deps": "Instalando dependencias del terminal-server...",
        "installed_terminal_deps": "Dependencias del terminal-server instaladas",
        "terminal_deps_failed": "Error al instalar dependencias del terminal-server",
        "services_user_note": "(los servicios se ejecutarán como {user})",
        "terminal_started": "Terminal server iniciado (puerto 32352)",
        "terminal_not_started": "Terminal server puede no haber iniciado — revisa logs/terminal-server.log",
        "dashboard_started": "Dashboard iniciado (puerto 8080)",
        "dashboard_not_started": "El dashboard puede no haber iniciado — revisa logs/dashboard.log",
        "setup_done": "¡Instalación completada!",
        "dashboard_available_at": "Dashboard disponible en:",
        "next_steps_header": "Próximos pasos:",
        "next_step_1_remote": "1. Abre el enlace de arriba y crea tu cuenta de administrador",
        "next_step_2_remote": "2. Ve a {bold}Proveedores{reset} y configura tu proveedor de IA",
        "next_step_3_remote": "3. ¡Abre un agente y comienza a usarlo!",
        "next_step_1_local": "1. Edita {bold}.env{reset} con tus claves de API",
        "next_step_2_local": "2. Ejecuta: {bold}make dashboard-app{reset}",
        "next_step_3_local": "3. Abre {bold}{url}{reset} y crea tu cuenta de administrador",
        "systemd_section": "Servicio systemd:",
        "systemd_status": "verificar estado",
        "systemd_restart": "reiniciar",
        "systemd_logs": "ver registros",
        "systemd_su": "entrar al usuario del servicio",
        # Prerequisites + tools
        "sys_packages_updating": "Actualizando paquetes del sistema...",
        "sys_packages_updated": "Paquetes del sistema actualizados",
        "tool_installed": "instalado",
        # Dashboard access wizard
        "local_only_option": "Solo local (http://localhost:8080)",
        "domain_ssl_option": "Dominio con SSL (recomendado para servidores remotos)",
        "type_1_or_2": "Escribe 1 o 2",
        "domain_prompt": "Dominio (ej. nexus.ejemplo.com)",
        "installing_nginx": "Instalando nginx...",
        "nginx_installed": "nginx instalado",
        "nginx_install_failed": "Error al instalar nginx, usando modo local",
        "ssl_cert_prompt": "Certificado SSL (1=certbot, 2=autofirmado, 3=ruta manual)",
        "cert_existing_found": "Certificado certbot existente encontrado para {domain}",
        "installing_certbot": "Instalando certbot...",
        "certbot_installed": "certbot instalado",
        "obtaining_ssl_certbot": "Obteniendo certificado SSL vía certbot...",
        "ssl_obtained_certbot": "Certificado SSL obtenido vía certbot",
        "certbot_failed_fallback": "certbot falló — usando autofirmado",
        "generating_self_signed": "Generando certificado SSL autofirmado...",
        "self_signed_generated": "Certificado SSL autofirmado generado",
        "self_signed_cloudflare_note": "(Compatible con el modo SSL Full de Cloudflare)",
        "self_signed_failed": "Error al generar el certificado SSL",
        "no_ssl_cert_local_mode": "Sin certificado SSL disponible, usando modo local",
        "manual_cert_prompt": "Ruta del certificado (.crt o .pem)",
        "manual_key_prompt": "Ruta de la clave privada (.key)",
        "nginx_configured_for": "Nginx configurado para {domain}",
        "nginx_config_failed": "Error al configurar nginx",
        "configuring_firewall": "Configurando firewall...",
        "firewall_ports_opened": "Puertos del firewall abiertos (80, 443)",
        "firewall_using_ufw": "Usando ufw",
        "firewall_using_iptables": "Usando iptables (ufw no está instalado)",
        "firewall_persisted": "Reglas persistidas vía {tool} (sobrevivirán al reinicio)",
        "firewall_persistence_missing": "Reglas abiertas solo en memoria — instala netfilter-persistent O ufw para persistir entre reinicios",
        "firewall_install_persistence": "Instalando netfilter-persistent para que las reglas sobrevivan al reinicio...",
        "firewall_failed": "El paso del firewall falló: {err}",
        "firewall_cloud_provider_hint": "Firewall del proveedor: si 80/443 siguen bloqueados desde fuera, ábrelos también en la Security List/Group de tu proveedor ({provider}).",
        # Workspace file creation
        "generated_workspace_yaml": "Generado config/workspace.yaml",
        "env_created_from_example": "Creado .env desde .env.example",
        "env_example_missing": ".env.example no encontrado, creando .env vacío",
        "env_already_exists": ".env ya existe, omitiendo",
        "generated_master_key": "KNOWLEDGE_MASTER_KEY generada (cifrado de la Knowledge Base)",
        "master_key_already_set": "KNOWLEDGE_MASTER_KEY ya definida — preservada",
        "master_key_skip_crypto_missing": "Generación de KNOWLEDGE_MASTER_KEY omitida ({exc})",
        "master_key_run_init_hint": "Ejecuta `make init-key` después del setup para generarla.",
        "master_key_ensure_failed": "No se pudo asegurar KNOWLEDGE_MASTER_KEY: {exc}",
        "generated_routines_yaml": "Creado config/routines.yaml",
        "routines_already_exists": "config/routines.yaml ya existe, omitiendo",
        "generated_claude_md": "Generado CLAUDE.md",
        "created_workspace_folders": "Carpetas del workspace creadas ({count})",
        # Systemd / service lifecycle
        "fixing_ownership": "Ajustando permisos de archivos para {user}...",
        "ownership_fixed": "Permisos ajustados",
        "starting_dashboard_services": "Iniciando servicios del dashboard...",
        "creating_systemd_service": "Creando servicio systemd...",
        "systemd_service_created": "Servicio systemd creado y habilitado (inicia al arrancar)",
        "systemd_manage_hint": "Administra con: systemctl {{start|stop|restart|status}} {service}",
        # Prerequisite tool check
        "tool_not_found": "{name} no encontrado",
        "tool_installing_verb": "Instalando {name}...",
        "tool_upgrading_verb": "Actualizando {name}...",
        "tool_install_failed": "Error al instalar {name}",
        "tool_upgrade_failed": "Error al actualizar {name}",
        "tool_required": "{name} es necesario para EvoNexus",
        "tool_install_manually": "{name} no encontrado — instálalo manualmente",
        "tool_skip_noninteractive": "Omitiendo instalación automática en modo no interactivo.",
        "tool_run_manually": "Ejecuta manualmente: {cmd}",
        "tool_install_prompt": "¿Instalar {name}? (S/n): ",
        "tool_upgrade_hint": "(actualizando a {required}+)",
        "installing_build_essential": "Instalando build-essential...",
        "build_essential_failed": "Error al instalar build-essential",
        "npm_not_found": "npm no encontrado (debería venir con Node.js)",
        "prereq_install_failed_header": "Las siguientes herramientas no pudieron instalarse:",
        "prereq_install_manually_retry": "Instálalas manualmente y ejecuta el setup de nuevo.",
        "invalid_choice_local_mode": "Opción inválida '{choice}'. Usando modo local.",
        "no_domain_local_mode": "No se proporcionó dominio, usando modo local",
        "nginx_config_test_failed": "La prueba de configuración de nginx falló",
        "nginx_config_saved_at": "La configuración se guardó en {path}",
        "nginx_fix_and_reload": "Corrige el problema y ejecuta: nginx -t && systemctl reload nginx",
        "nginx_config_not_created": "El archivo de configuración de nginx no se creó en {path}",
        "nginx_no_permission": "Sin permisos para escribir la configuración de nginx — ejecuta el setup como root/sudo",
        "removed_nginx_default_site": "Sitio predeterminado de nginx eliminado",
        # Install dir auto-relocation
        "install_inaccessible": "El usuario '{user}' no puede acceder a {path} (probablemente /root/* con modo 700)",
        "install_relocating": "Reubicando la instalación en {dest} para que el usuario del servicio pueda leerla...",
        "install_relocated": "Instalación reubicada en {dest}",
        "install_relocate_failed": "Error al reubicar la instalación — revisa espacio en disco / permisos",
        "install_relocate_hint": "La copia original en {orig} puede eliminarse cuando el setup termine",
        # AI Provider wizard
        "choose_ai_provider_header": "Elige tu proveedor de IA:",
        "provider_opt1_anthropic": "Anthropic (Claude nativo)",
        "provider_opt1_note": "predeterminado, sin configuración extra",
        "provider_opt2_openrouter": "OpenRouter (200+ modelos)",
        "provider_opt2_note": "requiere clave de API + openclaude",
        "provider_opt3_openai": "OpenAI (GPT-4.x / GPT-5.x)",
        "provider_opt3_note": "clave de API u OAuth + openclaude",
        "provider_opt4_gemini": "Google Gemini",
        "provider_opt5_bedrock": "AWS Bedrock",
        "provider_opt6_vertex": "Google Vertex AI",
        "provider_coming_soon_label": "próximamente",
        "provider_select_prompt": "Proveedor (1-3)",
        "provider_coming_soon_fallback": "Este proveedor estará disponible próximamente. Usando Anthropic por ahora.",
        "openclaude_not_found_for_provider": "openclaude no encontrado — necesario para {provider}",
        "install_now_prompt": "¿Instalar ahora? (s/n)",
        "provider_config_saved": "Configuración del proveedor guardada: {provider}",
        "provider_remember_logout": "Recuerda ejecutar /logout en Claude Code si estabas conectado a Anthropic",
        "openai_auth_header": "Autenticación de OpenAI",
        "openai_auth_opt_a": "Clave de API (GPT-4.x)",
        "openai_auth_opt_b": "Codex OAuth (GPT-5.x) — vía Dashboard",
        "openai_auth_method_prompt": "Método de autenticación (a/b)",
        "openai_provider_configured": "Proveedor configurado: OpenAI (Codex OAuth)",
        "openai_complete_via_dashboard": "Para completar la autenticación, abre el Dashboard",
        "openai_dashboard_path": "Proveedores → Iniciar sesión con OpenAI",
        "configure_provider_header": "Configurar {name}",
        "multi_select_hint": "Escribe las teclas para alternar (separadas por coma), o Enter para aceptar:",
        # Brain Repo (control de versiones)
        "brain_repo_enable_prompt": "¿Activar Brain Repo? (versiona tu memoria/workspace en GitHub)",
        "brain_repo_auth_method": "Método de autenticación",
        "brain_repo_defer_to_web": "Configurar en la interfaz web después",
        "brain_repo_pat_instructions": "Crea un PAT de GitHub en https://github.com/settings/tokens/new?scopes=repo",
        "brain_repo_pat_prompt": "Token de Acceso Personal de GitHub (alcance: repo)",
        "brain_repo_pat_saved": "PAT del Brain Repo guardado. Completa la configuración en la interfaz web.",
        "brain_repo_pat_skipped": "Omitido. Configura Brain Repo en Ajustes > Integraciones.",
        "brain_repo_configure_later": "¡De acuerdo! Conecta GitHub en Ajustes > Integraciones.",
    },
}


def T(key: str, **fmt) -> str:
    """Return a translated string for the active language with optional format args.

    Always runs through ``.format()`` so that translated strings can embed
    ``{bold}`` / ``{reset}`` placeholders even when the caller passes no
    extra args — otherwise those placeholders leak into the output as
    literal text (a regression seen in the "2. Vá em {bold}Provedores{reset}"
    line on the finished-setup screen).
    """
    bundle = MESSAGES.get(LANG) or MESSAGES["en-US"]
    # Fall back to en-US if a key is missing in the active bundle (defensive).
    text = bundle.get(key) or MESSAGES["en-US"].get(key) or key
    if not isinstance(text, str):
        return str(text)
    try:
        return text.format(bold=BOLD, reset=RESET, **fmt)
    except (KeyError, IndexError, ValueError):
        # Translated string uses a placeholder we didn't supply — return
        # the raw text rather than crashing the wizard.
        return text


def select_language() -> None:
    """Ask the user for their setup-wizard language. First prompt, always.

    Under non-interactive contexts (pip build backend, CI, `EVO_NEXUS_AUTO_INSTALL=1`)
    keep the default "en-US" to stay predictable — the dashboard later lets
    the user change it via the Settings UI.
    """
    global LANG
    auto = os.environ.get("EVO_NEXUS_AUTO_INSTALL") == "1"
    if not _IS_TTY or auto:
        return

    print()
    print(f"  {BOLD}{T('choose_lang_prompt')}{RESET}")
    print(f"    {BOLD}1{RESET}) {T('choose_lang_option_1')}")
    print(f"    {BOLD}2{RESET}) {T('choose_lang_option_2')}")
    print(f"    {BOLD}3{RESET}) {T('choose_lang_option_3')}")
    try:
        raw = input(f"  {T('choose_lang_ask')}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    mapping = {"1": "en-US", "2": "pt-BR", "3": "es"}
    LANG = mapping.get(raw, LANG)


def banner():
    """Draw a centered banner around the translated title.

    Width auto-adjusts to the title so translated versions stay aligned —
    em-dashes and accents are 1 display char each (we don't use any wide
    CJK glyphs here), so len(title) is the visible width.
    """
    title = T("banner_title")
    interior = max(36, len(title) + 6)           # at least 3 chars padding each side
    total_pad = interior - len(title)
    left = total_pad // 2
    right = total_pad - left
    hline = "═" * interior
    print(f"""
{GREEN}  ╔{hline}╗
  ║{' ' * left}{BOLD}{title}{RESET}{GREEN}{' ' * right}║
  ╚{hline}╝{RESET}
""")


def _parse_semver(s: str) -> tuple[int, int, int] | None:
    """Extract (major, minor, patch) from a version string. None on failure."""
    import re
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', s or "")
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (ValueError, TypeError):
        return None


def _check_tool(name, cmd, install_cmd=None, install_label=None, min_version=None):
    """Check if a tool is installed. If not, offer to install it.

    If min_version=(major, minor, patch) is given and the installed tool is
    older, treat it as missing and trigger the install path — this forces an
    upgrade when the pinned install_cmd specifies a newer version.

    In non-interactive contexts (pip build backend, npx pipe, CI) we skip
    the input() prompt — this is what fixes EOFError from upstream PR #11.
    When auto-confirm is appropriate (service user bootstrap), callers can
    pass EVO_NEXUS_AUTO_INSTALL=1 to proceed without prompting.
    """
    needs_upgrade = False
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip()
            if min_version is not None:
                parsed = _parse_semver(version)
                if parsed is not None and parsed < min_version:
                    required = ".".join(str(x) for x in min_version)
                    print(f"  {YELLOW}!{RESET} {name}: {DIM}{version}{RESET} {T('tool_upgrade_hint', required=required)}")
                    needs_upgrade = True
                else:
                    print(f"  {GREEN}✓{RESET} {name}: {DIM}{version}{RESET}")
                    return True
            else:
                print(f"  {GREEN}✓{RESET} {name}: {DIM}{version}{RESET}")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if install_cmd:
        if not needs_upgrade:
            print(f"  {YELLOW}!{RESET} {T('tool_not_found', name=name)}")
        # Non-interactive: skip the prompt entirely. Either auto-install
        # (when EVO_NEXUS_AUTO_INSTALL=1) or report missing.
        auto_install = os.environ.get("EVO_NEXUS_AUTO_INSTALL") == "1"
        # For upgrades we always proceed silently — the user already has
        # the tool and we just need a newer version.
        if needs_upgrade:
            choice = "y"
        elif not _IS_TTY and not auto_install:
            print(f"    {DIM}{T('tool_skip_noninteractive')}{RESET}")
            print(f"    {DIM}{T('tool_run_manually', cmd=install_cmd)}{RESET}")
            return False
        elif auto_install:
            choice = "y"
        else:
            choice = input(f"    {T('tool_install_prompt', name=name)}").strip().lower()
        if choice in ("", "y", "yes", "s", "sim"):
            if needs_upgrade:
                print(f"  {DIM}{T('tool_upgrading_verb', name=name)}{RESET}", end="", flush=True)
            else:
                print(f"  {DIM}{T('tool_installing_verb', name=name)}{RESET}", end="", flush=True)
            ret = os.system(f"{install_cmd} > /dev/null 2>&1")
            # Re-check after install
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    version = result.stdout.strip() or result.stderr.strip()
                    print(f"\r  {GREEN}✓{RESET} {name}: {DIM}{version}{RESET}                    ")
                    return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            fail_msg = T('tool_upgrade_failed', name=name) if needs_upgrade else T('tool_install_failed', name=name)
            print(f"\r  {RED}✗{RESET} {fail_msg}                    ")
        else:
            print(f"  {RED}✗{RESET} {T('tool_required', name=name)}")
    else:
        suffix = install_label or T('tool_install_manually', name=name)
        print(f"  {RED}✗{RESET} {T('tool_not_found', name=name)} — {suffix}")

    return False


def check_prerequisites():
    """Check and auto-install required tools."""
    # Update system packages first (ensures fresh package lists)
    if os.getuid() == 0:
        print(f"  {DIM}{T('sys_packages_updating')}{RESET}", end="", flush=True)
        os.system("DEBIAN_FRONTEND=noninteractive apt-get update -y -qq > /dev/null 2>&1")
        os.system("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' > /dev/null 2>&1")
        print(f"\r  {GREEN}✓{RESET} {T('sys_packages_updated')}       ")

    missing = []

    # build-essential (required for native npm packages like node-pty)
    try:
        result = subprocess.run(["g++", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"  {GREEN}✓{RESET} build-essential: {DIM}{T('tool_installed')}{RESET}")
        else:
            raise FileNotFoundError
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(f"  {DIM}{T('installing_build_essential')}{RESET}", end="", flush=True)
        os.system("apt install -y build-essential > /dev/null 2>&1 || yum groupinstall -y 'Development Tools' > /dev/null 2>&1")
        try:
            result = subprocess.run(["g++", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"  {GREEN}✓{RESET} build-essential: {DIM}{T('tool_installed')}{RESET}")
            else:
                print(f"  {RED}✗{RESET} {T('build_essential_failed')}")
                missing.append("build-essential")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"  {RED}✗{RESET} {T('build_essential_failed')}")
            missing.append("build-essential")

    # Node.js
    if not _check_tool("Node.js", ["node", "--version"],
                        install_cmd="curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && apt install -y nodejs 2>/dev/null || echo 'Install Node.js 18+ from https://nodejs.org'",
                        install_label="https://nodejs.org"):
        missing.append("node")

    # npm (comes with Node.js)
    npm_ok = False
    for cmd in ["npm", "npm.cmd"]:
        try:
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"  {GREEN}✓{RESET} npm: {DIM}v{result.stdout.strip()}{RESET}")
                npm_ok = True
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    if not npm_ok:
        print(f"  {RED}✗{RESET} {T('npm_not_found')}")
        missing.append("npm")

    # uv (Python package manager)
    # When running with sudo, install for the original user and add their
    # ~/.local/bin to root's PATH BEFORE verification
    _sudo_user_uv = os.environ.get("SUDO_USER", "")
    if _sudo_user_uv and os.getuid() == 0:
        # Resolve user home FIRST so we can find uv after install
        try:
            user_home = subprocess.run(["getent", "passwd", _sudo_user_uv], capture_output=True, text=True).stdout.split(":")[5]
        except (IndexError, Exception):
            user_home = f"/home/{_sudo_user_uv}"
        user_uv_bin = os.path.join(user_home, ".local", "bin")
        # Add user's bin to PATH before any uv checks
        if user_uv_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{user_uv_bin}:{os.environ.get('PATH', '')}"
        # Now check/install
        if not _check_tool("uv", ["uv", "--version"],
                            install_cmd=f"su - {_sudo_user_uv} -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'"):
            missing.append("uv")
    else:
        home_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
        if home_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{home_bin}:{os.environ.get('PATH', '')}"
        if not _check_tool("uv", ["uv", "--version"],
                            install_cmd="curl -LsSf https://astral.sh/uv/install.sh | sh"):
            missing.append("uv")

    # Claude Code CLI
    if not _check_tool("Claude Code CLI", ["claude", "--version"],
                        install_cmd="npm install -g @anthropic-ai/claude-code"):
        missing.append("claude")

    # OpenClaude (required for non-Anthropic providers)
    # min_version=(0, 3, 0) forces an upgrade on systems that already have
    # an older OpenClaude installed — v0.3.0 is the first release with the
    # "route OpenAI Codex shortcuts to correct endpoint" fix (#566) which
    # the codex_auth provider flow relies on.
    if not _check_tool("OpenClaude", ["openclaude", "--version"],
                        install_cmd="npm install -g @gitlawb/openclaude@latest",
                        min_version=(0, 3, 0)):
        missing.append("openclaude")

    print()

    if missing:
        print(f"  {RED}{T('prereq_install_failed_header')}{RESET}")
        for m in missing:
            print(f"    {RED}•{RESET} {m}")
        print(f"\n  {YELLOW}{T('prereq_install_manually_retry')}{RESET}")
        sys.exit(1)

    return True


def _detect_cloud_provider() -> str | None:
    """Best-effort cloud-provider detection for the firewall hint message.

    Looks at /sys/class/dmi/id/* (set by the BIOS/hypervisor) — non-fatal
    if unreadable. Returns a short human-readable label or None.
    """
    sources = [
        ("/sys/class/dmi/id/sys_vendor", {"oraclecloud": "Oracle Cloud (OCI)", "amazon ec2": "AWS EC2",
                                          "google": "Google Cloud", "microsoft": "Azure", "digitalocean": "DigitalOcean",
                                          "hetzner": "Hetzner Cloud"}),
        ("/sys/class/dmi/id/chassis_asset_tag", {"oraclecloud": "Oracle Cloud (OCI)",
                                                  "amazon": "AWS EC2", "google": "Google Cloud"}),
    ]
    for path, mapping in sources:
        try:
            with open(path) as f:
                value = f.read().strip().lower()
            for needle, label in mapping.items():
                if needle in value:
                    return label
        except OSError:
            continue
    return None


def _open_firewall_ports(ports: list[int]) -> None:
    """Open inbound TCP ports robustly and PERSISTENTLY.

    The previous one-liner used ``2>/dev/null`` everywhere, so any failure
    (ufw missing, iptables-nft refusing the rule, no permission) was
    silently swallowed and the wizard happily printed "Firewall ports
    opened" while nothing actually changed. Worse, it never persisted
    the rules, so on the first reboot the in-memory iptables additions
    vanished and the dashboard was unreachable from outside.

    Strategy:
      1. Prefer ``ufw`` when present — handles persistence itself.
      2. Otherwise use ``iptables -C`` to check before ``-I`` (idempotent
         re-runs on the same machine don't pile up duplicate rules).
      3. Persist via ``netfilter-persistent save`` if available; if not
         and we're on a Debian/Ubuntu system, install
         ``iptables-persistent`` non-interactively then save.
      4. If everything we tried fails, surface the actual error rather
         than reporting success.
      5. Always emit a hint about cloud-provider security lists — Oracle
         Cloud, AWS, GCP, etc. enforce a separate network firewall that
         no host-level command can bypass.
    """
    print(f"  {DIM}{T('configuring_firewall')}{RESET}")
    if os.getuid() != 0:
        # Non-root: we can't open the firewall anyway. Just hint.
        print(f"  {YELLOW}!{RESET} {T('firewall_persistence_missing')}")
        return

    backend_used = None
    errors: list[str] = []

    if shutil.which("ufw"):
        backend_used = "ufw"
        print(f"  {DIM}  {T('firewall_using_ufw')}{RESET}")
        for p in ports:
            rc = os.system(f"ufw allow {p}/tcp >/dev/null 2>&1")
            if rc != 0:
                errors.append(f"ufw allow {p}/tcp (rc={rc >> 8})")
    elif shutil.which("iptables"):
        backend_used = "iptables"
        print(f"  {DIM}  {T('firewall_using_iptables')}{RESET}")
        for p in ports:
            # -C tests whether the rule already exists; -I inserts at
            # the top of INPUT only when it doesn't (idempotent).
            check = os.system(f"iptables -C INPUT -p tcp --dport {p} -j ACCEPT >/dev/null 2>&1")
            if check != 0:
                rc = os.system(f"iptables -I INPUT -p tcp --dport {p} -j ACCEPT 2>/dev/null")
                if rc != 0:
                    errors.append(f"iptables -I {p} (rc={rc >> 8})")
    else:
        errors.append("neither ufw nor iptables available")

    # Persistence — the actual fix for "works after install, dies on reboot".
    persistence_tool = None
    if backend_used == "ufw":
        # ufw persists by default
        persistence_tool = "ufw"
    elif backend_used == "iptables":
        if shutil.which("netfilter-persistent"):
            rc = os.system("netfilter-persistent save >/dev/null 2>&1")
            if rc == 0:
                persistence_tool = "netfilter-persistent"
            else:
                errors.append(f"netfilter-persistent save (rc={rc >> 8})")
        elif shutil.which("apt-get"):
            print(f"  {DIM}  {T('firewall_install_persistence')}{RESET}")
            os.system(
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                "iptables-persistent netfilter-persistent >/dev/null 2>&1"
            )
            if shutil.which("netfilter-persistent"):
                rc = os.system("netfilter-persistent save >/dev/null 2>&1")
                if rc == 0:
                    persistence_tool = "netfilter-persistent"
                else:
                    errors.append(f"netfilter-persistent save (rc={rc >> 8})")
            else:
                # Last-resort manual save
                os.makedirs("/etc/iptables", exist_ok=True)
                rc = os.system("iptables-save > /etc/iptables/rules.v4 2>/dev/null")
                if rc == 0:
                    persistence_tool = "/etc/iptables/rules.v4"

    # Result reporting — no more silent success.
    if errors:
        print(f"  {YELLOW}!{RESET} {T('firewall_failed', err='; '.join(errors))}")
    else:
        print(f"  {GREEN}✓{RESET} {T('firewall_ports_opened')}")
    if persistence_tool:
        print(f"  {GREEN}✓{RESET} {T('firewall_persisted', tool=persistence_tool)}")
    else:
        print(f"  {YELLOW}!{RESET} {T('firewall_persistence_missing')}")

    cloud = _detect_cloud_provider()
    if cloud:
        print(f"  {DIM}  {T('firewall_cloud_provider_hint', provider=cloud)}{RESET}")


def configure_access() -> dict:
    """Configure how the dashboard will be accessed (local or domain with SSL)."""
    print(f"\n  {BOLD}{T('dashboard_access')}{RESET}\n")
    print(f"    {BOLD}1{RESET}) {T('local_only_option')}")
    print(f"    {BOLD}2{RESET}) {T('domain_ssl_option')}")

    choice = ask(T("type_1_or_2"), "1")

    if choice not in ("1", "2"):
        print(f"  {YELLOW}!{RESET} {T('invalid_choice_local_mode', choice=choice)}")
        return {"mode": "local", "url": "http://localhost:8080"}

    if choice == "1":
        return {"mode": "local", "url": "http://localhost:8080"}

    domain = ask(T("domain_prompt"), "")
    if not domain:
        print(f"  {YELLOW}!{RESET} {T('no_domain_local_mode')}")
        return {"mode": "local", "url": "http://localhost:8080"}
    # Clean up if user pasted a full URL
    domain = domain.strip().replace("http://", "").replace("https://", "").rstrip("/")

    # Step 1: Install nginx
    if not shutil.which("nginx"):
        print(f"  {DIM}{T('installing_nginx')}{RESET}", end="", flush=True)
        os.system("apt install -y nginx > /dev/null 2>&1 || yum install -y nginx > /dev/null 2>&1")
        if not shutil.which("nginx"):
            print(f"  {RED}✗{RESET} {T('nginx_install_failed')}")
            return {"mode": "local", "url": "http://localhost:8080"}
    print(f"  {GREEN}✓{RESET} {T('nginx_installed')}")

    # Step 2: Stop nginx to free port 80 for certbot
    os.system("systemctl stop nginx 2>/dev/null")

    # Step 3: SSL certificate — certbot by default, fallback to self-signed
    ssl_cert = ""
    ssl_key = ""

    ssl_mode = ask(T("ssl_cert_prompt"), "1")

    if ssl_mode == "1":
        certbot_cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        certbot_key = f"/etc/letsencrypt/live/{domain}/privkey.pem"

        # Reuse existing certbot cert if found
        if os.path.isfile(certbot_cert) and os.path.isfile(certbot_key):
            ssl_cert = certbot_cert
            ssl_key = certbot_key
            print(f"  {GREEN}✓{RESET} {T('cert_existing_found', domain=domain)}")
        else:
            # Install certbot if needed
            if not shutil.which("certbot"):
                print(f"  {DIM}{T('installing_certbot')}{RESET}", end="", flush=True)
                os.system("apt install -y certbot > /dev/null 2>&1")
                print(f"\r  {GREEN}✓{RESET} {T('certbot_installed')}                    ")
            # Obtain certificate (requires domain DNS pointing to this server)
            print(f"  {DIM}{T('obtaining_ssl_certbot')}{RESET}", end="", flush=True)
            ret = os.system(f"certbot certonly --standalone -d {domain} --non-interactive --agree-tos --register-unsafely-without-email > /dev/null 2>&1")
            if ret == 0:
                ssl_cert = certbot_cert
                ssl_key = certbot_key
                print(f"\r  {GREEN}✓{RESET} {T('ssl_obtained_certbot')}                    ")
            else:
                print(f"\r  {YELLOW}!{RESET} {T('certbot_failed_fallback')}                    ")
                ssl_mode = "2"

    if ssl_mode == "2":
        # Self-signed (works with Cloudflare Full mode)
        print(f"  {DIM}{T('generating_self_signed')}{RESET}")
        os.system("mkdir -p /etc/nginx/ssl")
        ret = os.system(f'openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -keyout /etc/nginx/ssl/{domain}.key -out /etc/nginx/ssl/{domain}.crt -subj "/CN={domain}" 2>/dev/null')
        if ret == 0:
            ssl_cert = f"/etc/nginx/ssl/{domain}.crt"
            ssl_key = f"/etc/nginx/ssl/{domain}.key"
            print(f"  {GREEN}✓{RESET} {T('self_signed_generated')}")
            print(f"  {DIM}{T('self_signed_cloudflare_note')}{RESET}")
        else:
            print(f"  {RED}✗{RESET} {T('self_signed_failed')}")

    if ssl_mode == "3":
        ssl_cert = ask(T("manual_cert_prompt"), f"/etc/nginx/ssl/{domain}.crt")
        ssl_key = ask(T("manual_key_prompt"), f"/etc/nginx/ssl/{domain}.key")

    # Fix SSL key permissions (nginx needs read access, restrict from others)
    if ssl_key and os.path.isfile(ssl_key):
        os.chmod(ssl_key, 0o600)

    if not ssl_cert or not ssl_key:
        print(f"  {RED}✗{RESET} {T('no_ssl_cert_local_mode')}")
        os.system("systemctl start nginx 2>/dev/null")
        return {"mode": "local", "url": "http://localhost:8080"}

    # Step 4: Write Nginx config with IPv6 support
    nginx_config = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {domain};

    ssl_certificate {ssl_cert};
    ssl_certificate_key {ssl_key};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }}

    location /terminal/ {{
        proxy_pass http://127.0.0.1:32352/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }}
}}
"""
    try:
        # Remove default nginx site if exists
        for default_site in ["/etc/nginx/sites-enabled/default", "/etc/nginx/conf.d/default.conf"]:
            if os.path.exists(default_site):
                os.remove(default_site)
                print(f"  {GREEN}✓{RESET} {T('removed_nginx_default_site')}")

        nginx_path = "/etc/nginx/sites-enabled/evonexus"
        with open(nginx_path, "w") as f:
            f.write(nginx_config)

        # Test nginx config
        ret = os.system("nginx -t 2>/tmp/nginx-test.log")
        if ret == 0:
            os.system("systemctl reload nginx 2>/dev/null || systemctl start nginx 2>/dev/null")
            os.system("systemctl enable nginx 2>/dev/null")
            print(f"  {GREEN}✓{RESET} {T('nginx_configured_for', domain=domain)}")
        else:
            # nginx -t failed — likely SSL cert issue. Show the error clearly.
            print(f"  {RED}✗{RESET} {T('nginx_config_test_failed')}")
            os.system("cat /tmp/nginx-test.log 2>/dev/null")
            print(f"    {YELLOW}{T('nginx_config_saved_at', path=nginx_path)}{RESET}")
            print(f"    {YELLOW}{T('nginx_fix_and_reload')}{RESET}")

        # Verify the config file actually exists after writing
        if not os.path.exists(nginx_path):
            print(f"  {RED}✗{RESET} {T('nginx_config_not_created', path=nginx_path)}")
    except PermissionError:
        print(f"  {YELLOW}!{RESET} {T('nginx_no_permission')}")

    # Step 5: Open firewall ports
    _open_firewall_ports([80, 443, 8080, 32352])

    return {"mode": "domain", "url": f"https://{domain}"}


def choose_provider() -> str:
    """Ask the user which AI provider to use."""
    coming_soon = T('provider_coming_soon_label')
    print(f"""
  {T('choose_ai_provider_header')}

    {BOLD}1{RESET}) {T('provider_opt1_anthropic')}         — {T('provider_opt1_note')}
    {BOLD}2{RESET}) {T('provider_opt2_openrouter')}           — {T('provider_opt2_note')}
    {BOLD}3{RESET}) {T('provider_opt3_openai')}         — {T('provider_opt3_note')}
    {BOLD}4{RESET}) {T('provider_opt4_gemini')}                      — {coming_soon}
    {BOLD}5{RESET}) {T('provider_opt5_bedrock')}                        — {coming_soon}
    {BOLD}6{RESET}) {T('provider_opt6_vertex')}                   — {coming_soon}
""")
    choice = ask(T('provider_select_prompt'), "1")
    provider_map = {
        "1": "anthropic", "2": "openrouter", "3": "openai",
    }
    if choice in ("4", "5", "6"):
        print(f"  {YELLOW}!{RESET} {T('provider_coming_soon_fallback')}")
        choice = "1"
    provider_id = provider_map.get(choice, "anthropic")

    # Check if openclaude is needed
    if provider_id != "anthropic":
        try:
            result = subprocess.run(["openclaude", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"  {GREEN}✓{RESET} openclaude: {DIM}{result.stdout.strip()}{RESET}")
            else:
                raise FileNotFoundError
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"  {YELLOW}!{RESET} {T('openclaude_not_found_for_provider', provider=provider_id)}")
            print(f"    {DIM}npm install -g @gitlawb/openclaude{RESET}")
            install = ask(T('install_now_prompt'), "y")
            if install.lower() == "y":
                os.system("npm install -g @gitlawb/openclaude")

    # Load base config
    providers_file = WORKSPACE / "config" / "providers.json"
    if providers_file.exists():
        import json as _json
        config = _json.loads(providers_file.read_text(encoding="utf-8"))
    else:
        # Read from template
        config = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {"name": "Anthropic (Claude nativo)", "cli_command": "claude", "env_vars": {}, "requires_logout": False},
                "openrouter": {"name": "OpenRouter", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_BASE_URL": "", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""}, "default_base_url": "https://openrouter.ai/api/v1", "default_model": "anthropic/claude-sonnet-4", "requires_logout": True},
                "openai": {"name": "OpenAI (API Key)", "description": "GPT-4.x / GPT-5.x via API Key da OpenAI", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""}, "default_model": "gpt-4.1", "requires_logout": True},
                "codex_auth": {"name": "OpenAI Codex (OAuth)", "description": "GPT-5.x via ChatGPT Codex — login OAuth, sem API key", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_MODEL": "codexplan"}, "default_model": "codexplan", "auth_type": "oauth", "auth_file": "~/.codex/auth.json", "requires_logout": True, "setup_hint": "Use o botão Login para autenticar via OAuth do ChatGPT"},
                "gemini": {"name": "Google Gemini (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_GEMINI": "1", "GEMINI_API_KEY": "", "GEMINI_MODEL": ""}, "default_model": "gemini-2.5-pro", "requires_logout": True, "coming_soon": True},
                "bedrock": {"name": "AWS Bedrock (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "", "AWS_BEARER_TOKEN_BEDROCK": ""}, "requires_logout": True, "coming_soon": True},
                "vertex": {"name": "Google Vertex AI (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "", "CLOUD_ML_REGION": ""}, "default_region": "us-east5", "requires_logout": True, "coming_soon": True},
            }
        }

    # Collect env vars for the chosen provider
    prov = config["providers"].get(provider_id, {})
    env_vars = prov.get("env_vars", {})

    if provider_id == "openai":
        print(f"\n  {BOLD}{T('openai_auth_header')}{RESET}")
        print(f"    {BOLD}a{RESET}) {T('openai_auth_opt_a')}")
        print(f"    {BOLD}b{RESET}) {T('openai_auth_opt_b')}")
        auth_choice = ask(T('openai_auth_method_prompt'), "b")

        if auth_choice.lower() == "a":
            api_key = ask("  OPENAI_API_KEY", "")
            model = ask("  OPENAI_MODEL", prov.get("default_model", "gpt-4.1"))
            env_vars = {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_API_KEY": api_key, "OPENAI_MODEL": model}
        else:
            model = ask("  OPENAI_MODEL", "gpt-5.4")
            env_vars = {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_MODEL": model}
            print(f"\n  {GREEN}✓{RESET} {T('openai_provider_configured')}")
            print(f"  {YELLOW}!{RESET} {T('openai_complete_via_dashboard')}")
            print(f"    {BOLD}{T('openai_dashboard_path')}{RESET}")

        prov["env_vars"] = env_vars

    elif provider_id != "anthropic":
        print(f"\n  {BOLD}{T('configure_provider_header', name=prov.get('name', provider_id))}{RESET}")
        for key, current in env_vars.items():
            if key.startswith("CLAUDE_CODE_USE_"):
                continue
            default = prov.get("default_base_url", "") if key == "OPENAI_BASE_URL" else prov.get("default_model", "") if "MODEL" in key else prov.get("default_region", "") if "REGION" in key else current
            value = ask(f"  {key}", default)
            env_vars[key] = value

        prov["env_vars"] = env_vars

    # Save
    config["active_provider"] = provider_id
    import json as _json
    (WORKSPACE / "config").mkdir(exist_ok=True)
    (WORKSPACE / "config" / "providers.json").write_text(
        _json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  {GREEN}✓{RESET} {T('provider_config_saved', provider=provider_id)}")

    if prov.get("requires_logout"):
        print(f"  {YELLOW}!{RESET} {T('provider_remember_logout')}")

    return provider_id


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {CYAN}>{RESET} {prompt}{suffix}: ").strip()
    return val or default


def ask_bool(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    val = input(f"  {CYAN}>{RESET} {prompt} {suffix}: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "1", "true")


def ask_password(prompt: str) -> str:
    """Read a password/token without echoing it to the terminal."""
    import getpass
    try:
        val = getpass.getpass(f"  {CYAN}>{RESET} {prompt}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        val = ""
    return val.strip()


def ask_choice(prompt: str, options: list[str], default: int = 0) -> str:
    """Present a numbered list and return the chosen option string."""
    print(f"\n  {prompt}")
    for i, opt in enumerate(options, 1):
        print(f"    {BOLD}{i}{RESET}) {opt}")
    raw = ask(f"Choice (1-{len(options)})", str(default + 1))
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except (ValueError, TypeError):
        pass
    return options[default]


def _ensure_brain_master_key() -> None:
    """Generate BRAIN_REPO_MASTER_KEY in .env if not already present."""
    env_file = WORKSPACE / ".env"
    existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if "BRAIN_REPO_MASTER_KEY" in existing:
        return
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"\nBRAIN_REPO_MASTER_KEY={key}\n")
        # Expose to current process so _save_brain_repo_pat can use it
        os.environ["BRAIN_REPO_MASTER_KEY"] = key
        print(f"  {GREEN}✓{RESET} BRAIN_REPO_MASTER_KEY generated")
    except ImportError:
        print(f"  {YELLOW}!{RESET} cryptography not installed — skipping key generation")


def _save_brain_repo_pat(pat: str) -> None:
    """Save encrypted PAT to brain_repo_configs table for user_id=1 (first admin)."""
    import sqlite3
    master_key = os.environ.get("BRAIN_REPO_MASTER_KEY", "").encode()
    if not master_key:
        print(f"  {YELLOW}!{RESET} BRAIN_REPO_MASTER_KEY not set — PAT not saved")
        return
    try:
        from cryptography.fernet import Fernet
        encrypted = Fernet(master_key).encrypt(pat.encode())
    except Exception as exc:
        print(f"  {YELLOW}!{RESET} Encryption failed: {exc}")
        return

    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    if not db_path.exists():
        # DB created when the backend starts; store the PAT in a temp file
        # that the backend will import on first boot.
        pat_pending = WORKSPACE / "dashboard" / "data" / "brain_repo_pat_pending.enc"
        pat_pending.parent.mkdir(parents=True, exist_ok=True)
        pat_pending.write_bytes(encrypted)
        print(f"  {DIM}  PAT queued — will be imported when dashboard starts{RESET}")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        # Ensure the table exists (migration may not have run yet)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brain_repo_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                github_token_encrypted BLOB,
                repo_url TEXT,
                repo_owner TEXT,
                repo_name TEXT,
                local_path TEXT,
                last_sync TIMESTAMP,
                sync_enabled INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                pending_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Insert or update for the first user (id=1)
        cur.execute("""
            INSERT INTO brain_repo_configs (user_id, github_token_encrypted, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                github_token_encrypted=excluded.github_token_encrypted,
                updated_at=CURRENT_TIMESTAMP
        """, (encrypted,))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"  {YELLOW}!{RESET} Could not save PAT to database: {exc}")


def ask_multi(prompt: str, options: list[dict], defaults: list[str] = None) -> list[str]:
    """Multi-select with checkboxes."""
    if defaults is None:
        defaults = []
    selected = set(defaults)

    print(f"\n  {prompt}")
    for opt in options:
        key = opt["key"]
        label = opt["label"]
        desc = opt.get("desc", "")
        checked = "x" if key in selected else " "
        desc_str = f" — {DIM}{desc}{RESET}" if desc else ""
        print(f"  [{checked}] {label}{desc_str}")

    print(f"\n  {DIM}{T('multi_select_hint')}{RESET}")
    val = input(f"  {CYAN}>{RESET} ").strip()

    if val:
        for key in val.replace(" ", "").split(","):
            key = key.strip().lower()
            if key in {o["key"] for o in options}:
                if key in selected:
                    selected.discard(key)
                else:
                    selected.add(key)

    return list(selected)


AGENTS = [
    {"key": "ops", "label": "ops", "desc": "Daily operations (briefing, email, tasks)"},
    {"key": "finance", "label": "finance", "desc": "Financial (P&L, cash flow, invoices)"},
    {"key": "projects", "label": "projects", "desc": "Project management (sprints, milestones)"},
    {"key": "community", "label": "community", "desc": "Community (Discord, WhatsApp pulse)"},
    {"key": "social", "label": "social", "desc": "Social media (content, analytics)"},
    {"key": "strategy", "label": "strategy", "desc": "Strategy (OKRs, roadmap)"},
    {"key": "sales", "label": "sales", "desc": "Commercial (pipeline, proposals)"},
    {"key": "courses", "label": "courses", "desc": "Education (course creation)"},
    {"key": "personal", "label": "personal", "desc": "Personal (health, habits)"},
]

INTEGRATIONS = [
    {"key": "google_calendar", "label": "Google Calendar + Gmail"},
    {"key": "todoist", "label": "Todoist"},
    {"key": "discord", "label": "Discord"},
    {"key": "telegram", "label": "Telegram"},
    {"key": "whatsapp", "label": "WhatsApp"},
    {"key": "stripe", "label": "Stripe"},
    {"key": "omie", "label": "Omie ERP"},
    {"key": "github", "label": "GitHub"},
    {"key": "linear", "label": "Linear"},
    {"key": "youtube", "label": "YouTube"},
    {"key": "instagram", "label": "Instagram"},
    {"key": "linkedin", "label": "LinkedIn"},
    {"key": "fathom", "label": "Fathom (meetings)"},
]

DEFAULT_FOLDERS = {
    "daily_logs": "daily-logs",
    "projects": "projects",
    "community": "community",
    "social": "social",
    "finance": "finance",
    "meetings": "meetings",
    "courses": "courses",
    "strategy": "strategy",
}


def generate_workspace_yaml(config: dict) -> str:
    lines = [
        "# EvoNexus Workspace Configuration",
        f"# Generated by setup.py on {config['date']}",
        "",
        f'name: "{config["workspace_name"]}"',
        f'owner: "{config["owner_name"]}"',
        f'company: "{config["company_name"]}"',
        f'timezone: "{config["timezone"]}"',
        f'language: "{config["language"]}"',
        f"port: {config['dashboard_port']}",
        "",
    ]
    return "\n".join(lines)


def generate_claude_md(config: dict) -> str:
    """Generate CLAUDE.md inline — no template file needed."""
    agent_table = ""
    for agent in AGENTS:
        if agent["key"] in config["agents"]:
            agent_table += f"| **{agent['label'].title()}** | `/{agent['key']}` | {agent['desc']} |\n"

    skill_count = len(list((WORKSPACE / ".claude" / "skills").iterdir())) if (WORKSPACE / ".claude" / "skills").is_dir() else 0

    return f"""# {config['workspace_name']} — Claude Context File

Claude reads this file at the start of every session. It's your persistent memory.

---

## How This Workspace Works

This workspace exists to produce things, not just store them. Everything here is oriented around a loop: **define a goal → break it into problems → solve those problems → deliver the output.**

Claude's role is to keep {(config['owner_name'].split()[0] if config['owner_name'].strip() else 'the user')} moving through this loop. If there's no goal yet, help define one. If there's a goal but no clear problems, help break it down. If there are problems, help solve the next one. Always push toward the next concrete thing to do or deliver.

---

## Who I Am

**Name:** {config['owner_name']}
**Company:** {config['company_name']}
**Timezone:** {config['timezone']}

---

## Active Projects

| Name | What it is | Status |
|------|---------|--------|
| *(add your projects here)* | | |

---

## Active Agents

| Agent | Command | Domain |
|-------|---------|--------|
{agent_table}
## Skills ({skill_count} skills)

See `.claude/skills/CLAUDE.md` for the complete index.

## What Claude Should Do

- **Always respond in {config['language']}.** This applies to every message, every session, without exception.
- Maintain a professional, clear and well-organized tone.
- Before working on any area, read the corresponding Overview file.
- Outputs for each area go in the correct folder. If unsure, ask.
- When creating files, prefix with [C] to indicate Claude created it.
- Use the correct agents for each domain (see agents table above).
- Use skills with the correct prefix (see `.claude/skills/CLAUDE.md`).

## What Claude Should NOT Do

- Do not edit notes without asking permission. Only files with [C] prefix are free to edit.
- Do not be verbose — be direct and concrete.
- Do not create projects without first interviewing the user about the objective and context.
- Do not overwrite existing skills or templates without confirming.

---

## Memory (Hot Cache)

### Me
{config['owner_name']} — {config['company_name']}

### People
| Who | Role |
|-----|------|
| *(add key people here)* | |
→ Full profiles: memory/people/

### Terms
| Term | Meaning |
|------|---------|
| *(add internal terms here)* | |
→ Full glossary: memory/glossary.md

### Preferences
- Always respond in {config['language']}
- Timezone: {config['timezone']}
- Tone: professional and direct

---

## Memory System

Two-tier memory following the **LLM Wiki pattern** (ingest → query → lint):

- **CLAUDE.md** (this file) — Hot cache with key people, terms, projects (~90% of daily needs)
- **memory/** — Deep storage with full profiles, glossary, project details, trends
  - `index.md` — Centralized catalog of all memory files (auto-updated)
  - `log.md` — Append-only chronological record of all memory operations
  - `glossary.md` — Full decoder ring for internal language
  - `people/` — Complete people profiles
  - `projects/` — Project details and context
  - `context/` — Company, teams, tools

Three operations maintain the knowledge base:
- **Ingest** (daily, memory-sync) — Extracts knowledge and propagates updates across related files
- **Query** (conversations) — Complex syntheses filed back as new entries
- **Lint** (weekly, memory-lint) — Detects contradictions, stale data, orphans, and gaps

---

## Detailed Configuration

See `.claude/rules/` for detailed configuration (auto-loaded by Claude Code):
- `agents.md` — specialized agents and how to use them
- `integrations.md` — MCPs, APIs, GitHub repos, infra and templates
- `routines.md` — daily, weekly and monthly scheduler routines
- `skills.md` — skill categories and prefixes

---

*Claude updates this file as the workspace grows. You can also edit it at any time.*
"""


def copy_env_example(config: dict):
    src = WORKSPACE / ".env.example"
    dst = WORKSPACE / ".env"
    if dst.exists():
        print(f"  {YELLOW}!{RESET} {T('env_already_exists')}")
        return
    if src.exists():
        shutil.copy2(src, dst)
        print(f"  {GREEN}✓{RESET} {T('env_created_from_example')}")
    else:
        print(f"  {YELLOW}!{RESET} {T('env_example_missing')}")
        dst.write_text("# EvoNexus Environment Variables\n# Fill in your API keys below\n\n", encoding="utf-8")


def ensure_knowledge_master_key(config: dict):
    """Generate KNOWLEDGE_MASTER_KEY on first setup so the Knowledge Base
    (pgvector-knowledge) works out of the box — no need for the user to run
    `evonexus init-key` manually.

    Idempotent: a key that already exists is preserved.
    Safe: failure is non-fatal — Knowledge features will error clearly at
    first use and the user can still run `make init-key` manually.
    """
    env_path = WORKSPACE / ".env"
    try:
        # Import lazily so a missing cryptography (pre-`uv sync`) does not
        # break the rest of the setup wizard.
        sys.path.insert(0, str(WORKSPACE / "dashboard" / "backend"))
        from knowledge.cli import ensure_master_key
        was_generated, _ = ensure_master_key(env_path)
    except RuntimeError as exc:
        # cryptography not installed yet — acceptable; uv sync will fix
        # on the next line and the user can re-run this step if needed.
        print(f"  {YELLOW}!{RESET} {T('master_key_skip_crypto_missing', exc=exc)}")
        print(f"    {DIM}{T('master_key_run_init_hint')}{RESET}")
        return
    except Exception as exc:  # noqa: BLE001 — never block setup on this
        print(f"  {YELLOW}!{RESET} {T('master_key_ensure_failed', exc=exc)}")
        print(f"    {DIM}{T('master_key_run_init_hint')}{RESET}")
        return

    if was_generated:
        print(f"  {GREEN}✓{RESET} {T('generated_master_key')}")
    else:
        print(f"  {DIM}  {T('master_key_already_set')}{RESET}")


def copy_routines_config(config: dict):
    dst = WORKSPACE / "config" / "routines.yaml"
    if dst.exists():
        print(f"  {YELLOW}!{RESET} {T('routines_already_exists')}")
        return
    # Try example file first, otherwise generate minimal config
    src = WORKSPACE / "config" / "routines.yaml.example"
    if src.exists():
        shutil.copy2(src, dst)
    else:
        dst.write_text("# EvoNexus Routines — edit schedules here\n# See ROUTINES.md for documentation\n\ndaily: []\nweekly: []\nmonthly: []\n", encoding="utf-8")
    print(f"  {GREEN}✓{RESET} {T('generated_routines_yaml')}")


def create_folders(config: dict):
    count = 0
    for key, name in config["folders"].items():
        folder = WORKSPACE / name
        folder.mkdir(exist_ok=True)
        gitkeep = folder / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()
        count += 1

    # Data dirs
    for d in ["data", "memory"]:
        (WORKSPACE / d).mkdir(exist_ok=True)

    print(f"  {GREEN}✓{RESET} {T('created_workspace_folders', count=count)}")


def _ensure_user_has_tools(service_user: str) -> None:
    """Make sure ``uv`` (and friends) are installed for ``service_user``.

    The wizard's ``check_prerequisites()`` installs uv / claude / openclaude
    for whoever ran the wizard (root, in the VPS case). When the service
    will later run as a different user (``SUDO_USER=ubuntu``, or the
    auto-created ``evonexus`` account), that user has its own ``$HOME``
    and inherits none of those tools — so ``su - <user> -c 'uv sync'``
    fails immediately with "uv: command not found".

    Idempotent: each ``command -v`` check skips the install when the
    tool is already present. Safe to call from both the SUDO_USER and
    auto-created-user branches.
    """
    if not service_user or service_user == "root":
        return
    # uv (Python package manager) — needed for `uv sync`
    rc = os.system(
        f"su - {service_user} -c 'export PATH=$HOME/.local/bin:$PATH && command -v uv' "
        f">/dev/null 2>&1"
    )
    if rc != 0:
        print(f"  {DIM}{T('tool_installing_verb', name=f'uv for {service_user}')}{RESET}")
        os.system(
            f"su - {service_user} -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' "
            f">/dev/null 2>&1"
        )
    # Claude Code CLI — needed by the dashboard at runtime
    rc = os.system(
        f"su - {service_user} -c 'export PATH=$HOME/.local/bin:$PATH && command -v claude' "
        f">/dev/null 2>&1"
    )
    if rc != 0:
        print(f"  {DIM}{T('tool_installing_verb', name=f'Claude Code for {service_user}')}{RESET}")
        os.system(
            f"su - {service_user} -c 'npm install -g @anthropic-ai/claude-code --prefix ~/.local' "
            f">/dev/null 2>&1"
        )
    # OpenClaude — required for non-Anthropic providers (OpenAI, Gemini, ...)
    rc = os.system(
        f"su - {service_user} -c 'export PATH=$HOME/.local/bin:$PATH && command -v openclaude' "
        f">/dev/null 2>&1"
    )
    if rc != 0:
        print(f"  {DIM}{T('tool_installing_verb', name=f'OpenClaude for {service_user}')}{RESET}")
        os.system(
            f"su - {service_user} -c 'npm install -g @gitlawb/openclaude@latest --prefix ~/.local' "
            f">/dev/null 2>&1"
        )


def _maybe_relocate_install(install_dir: Path) -> Path:
    """Relocate the install when the future service user can't reach it.

    Common failure on VPS: operator runs the wizard as root after
    ``sudo`` (so ``SUDO_USER=ubuntu`` is set) from ``/root/evonexus``.
    ``/root`` is mode 700, so the ``ubuntu`` user cannot ``chdir`` into
    the install directory. Symptoms:
      * ``su - ubuntu -c 'cd /root/evonexus && uv sync'`` fails →
        "Failed to install Python dependencies"
      * systemd unit later fails with ``status=200/CHDIR``

    Auto-fix: copy the project into ``/home/<service_user>/evo-nexus``
    and update the global ``WORKSPACE`` so every later step (deps, npm
    build, systemd unit path) sees the new location.

    Returns the (possibly new) install directory. Called BEFORE the
    Python dep install so the relocation cascades through the rest of
    the wizard automatically.
    """
    global WORKSPACE
    sudo_user = os.environ.get("SUDO_USER", "")
    if os.getuid() != 0 or not sudo_user or sudo_user == "root":
        return install_dir
    # Cheap reachability test: can the service user actually read+enter it?
    rc = os.system(
        f"su - {sudo_user} -c 'test -x {install_dir} && test -r {install_dir}/setup.py' "
        f">/dev/null 2>&1"
    )
    if rc == 0:
        return install_dir
    try:
        service_home = subprocess.run(
            ["getent", "passwd", sudo_user], capture_output=True, text=True, timeout=5
        ).stdout.strip().split(":")[5]
    except (IndexError, subprocess.SubprocessError):
        service_home = f"/home/{sudo_user}"
    if not service_home:
        service_home = f"/home/{sudo_user}"
    new_dir = Path(service_home) / "evo-nexus"
    print(f"\n  {YELLOW}!{RESET} {T('install_inaccessible', user=sudo_user, path=install_dir)}")
    print(f"  {DIM}{T('install_relocating', dest=new_dir)}{RESET}")
    if new_dir.exists():
        os.system(f"rm -rf {new_dir}")
    rc = os.system(f"cp -a {install_dir} {new_dir}")
    if rc != 0:
        print(f"  {RED}✗{RESET} {T('install_relocate_failed')}")
        return install_dir
    os.system(f"chown -R {sudo_user}:{sudo_user} {new_dir}")
    print(f"  {GREEN}✓{RESET} {T('install_relocated', dest=new_dir)}")
    print(f"  {DIM}{T('install_relocate_hint', orig=install_dir)}{RESET}")
    WORKSPACE = new_dir
    try:
        os.chdir(new_dir)
    except OSError:
        pass
    # Bootstrap uv/claude/openclaude for the service user — without this
    # the very next step (`su - ubuntu -c 'uv sync'`) fails with
    # "command not found" because uv lives under root's $HOME.
    _ensure_user_has_tools(sudo_user)
    return new_dir


def _setup_systemd_service(service_user, install_dir, logs_dir):
    """Create and start a systemd service for EvoNexus."""
    service_home = f"/home/{service_user}"
    service_name = "evo-nexus"
    service_file = f"/etc/systemd/system/{service_name}.service"

    print(f"  {DIM}{T('creating_systemd_service')}{RESET}")

    with open(service_file, "w") as f:
        f.write(f"""[Unit]
Description=EvoNexus Dashboard + Scheduler + Terminal Server
After=network.target
Documentation=https://github.com/EvolutionAPI/evo-nexus

[Service]
Type=oneshot
RemainAfterExit=yes
KillMode=none
User={service_user}
Group={service_user}
WorkingDirectory={install_dir}
Environment=PATH={service_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME={service_home}
ExecStart=/bin/bash {install_dir}/start-services.sh
ExecStop=/bin/bash -c 'pkill -f "terminal-server/bin/server.js" 2>/dev/null; pkill -f "dashboard/backend.*app.py" 2>/dev/null'
StandardOutput=append:{logs_dir}/service.log
StandardError=append:{logs_dir}/service.log

[Install]
WantedBy=multi-user.target
""")

    os.system("systemctl daemon-reload")
    os.system(f"systemctl enable {service_name} >/dev/null 2>&1")
    os.system(f"systemctl start {service_name}")
    print(f"  {GREEN}✓{RESET} {T('systemd_service_created')}")
    print(f"  {DIM}  {T('systemd_manage_hint', service=service_name)}{RESET}")


def main():
    # Ask for the wizard language before ANY other prompt so every
    # subsequent message renders in the user's choice.
    select_language()

    banner()

    # Prerequisites check
    print(f"  {BOLD}{T('checking_prereqs')}{RESET}")
    check_prerequisites()

    # Dashboard access (Nginx config) — FIRST post-banner question
    access_config = configure_access()
    is_remote = access_config.get("mode") == "domain"

    if is_remote:
        # Remote mode: minimal setup, then redirect to dashboard
        print(f"\n  {BOLD}{T('quick_remote_setup')}{RESET}")
        owner_name = ""
        company_name = ""
        timezone = "America/Sao_Paulo"
        # Match the language the user just chose in select_language()
        # so the dashboard opens in the same tongue on first login.
        language = LANG
        dashboard_port = 8080
    else:
        # Local mode: full interactive setup
        # Provider choice
        print(f"  {BOLD}{T('ai_provider')}{RESET}")
        provider_choice = choose_provider()
        print()

        # ── Brain Repo (versioning) ────────────────────────
        brain_repo_enabled = ask_bool(T("brain_repo_enable_prompt"), default=False)
        if brain_repo_enabled:
            brain_method = ask_choice(
                T("brain_repo_auth_method"),
                options=["PAT", T("brain_repo_defer_to_web")],
            )
            if brain_method == "PAT":
                print(f"\n{T('brain_repo_pat_instructions')}")
                pat = ask_password(T("brain_repo_pat_prompt"))
                if pat:
                    # Generate BRAIN_REPO_MASTER_KEY if not in env
                    _ensure_brain_master_key()
                    # Save PAT — will be completed in the web wizard
                    _save_brain_repo_pat(pat)
                    print(f"\n{T('brain_repo_pat_saved')}")
                else:
                    print(T("brain_repo_pat_skipped"))
            else:
                print(f"\n{T('brain_repo_configure_later')}")
        print()

        # Who are you?
        print(f"  {BOLD}{T('about_you')}{RESET}")
        owner_name = ask(T("your_name"), "")
        company_name = ask(T("company_name"), "")
        timezone = ask(T("timezone"), "America/Sao_Paulo")
        language = ask(T("language"), LANG)
        dashboard_port = int(ask(T("dashboard_port"), "8080"))
        print()

    # All agents and integrations enabled by default
    agents = [a["key"] for a in AGENTS]
    integrations = []  # configured via .env later

    # Build config
    from datetime import date
    config = {
        "date": date.today().isoformat(),
        "workspace_name": f"{company_name or owner_name} Workspace",
        "owner_name": owner_name,
        "company_name": company_name,
        "timezone": timezone,
        "language": language,
        "agents": agents,
        "integrations": integrations,
        "folders": DEFAULT_FOLDERS.copy(),
        "dashboard_port": dashboard_port,
    }

    print(f"  {BOLD}{T('creating_workspace')}{RESET}")

    # workspace.yaml
    config_dir = WORKSPACE / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "workspace.yaml").write_text(generate_workspace_yaml(config), encoding="utf-8")
    print(f"  {GREEN}✓{RESET} {T('generated_workspace_yaml')}")

    # .env
    copy_env_example(config)

    # KNOWLEDGE_MASTER_KEY (idempotent; generated on first setup)
    ensure_knowledge_master_key(config)

    # routines.yaml
    copy_routines_config(config)

    # CLAUDE.md
    claude_md = generate_claude_md(config)
    (WORKSPACE / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    print(f"  {GREEN}✓{RESET} {T('generated_claude_md')}")

    # Folders
    create_folders(config)

    # Logs dir (for install logs)
    (WORKSPACE / "logs").mkdir(exist_ok=True)

    # Auto-relocate the install if the future service user (SUDO_USER) cannot
    # reach it — typically because the operator cloned into /root/* (mode 700).
    # This MUST run before `uv sync`, npm install, and the systemd unit gets
    # written, otherwise all three fail with permission errors.
    _maybe_relocate_install(WORKSPACE)

    # Install Python dependencies
    # Must run as the ORIGINAL user (not root) so .venv symlinks
    # point to user's Python, not /root/.local/share/uv/python/
    print(f"  {DIM}{T('installing_python_deps')}{RESET}", end="", flush=True)
    _sudo_user = os.environ.get("SUDO_USER", "")
    if _sudo_user and os.getuid() == 0:
        # Add ~/.local/bin to PATH explicitly. ``su - <user>`` runs the
        # login shell but Ubuntu's default ~/.profile only adds
        # ~/.local/bin to PATH for INTERACTIVE shells, so a non-interactive
        # ``su -c '...'`` may not see the ``uv`` we just installed for them.
        ret = os.system(
            f"su - {_sudo_user} -c "
            f"'export PATH=$HOME/.local/bin:$PATH && cd {WORKSPACE} && uv sync -q' "
            f"2>{WORKSPACE}/logs/uv-sync.log"
        )
    else:
        ret = os.system(f"cd {WORKSPACE} && uv sync -q 2>{WORKSPACE}/logs/uv-sync.log")
    if ret == 0 and (WORKSPACE / ".venv" / "bin" / "python").exists():
        print(f"\r  {GREEN}✓{RESET} {T('installed_python_deps')}                    ")
    else:
        print(f"\r  {RED}✗{RESET} {T('python_deps_failed')}                    ")
        print(f"    {YELLOW}{T('python_deps_needed')}{RESET}")
        print(f"    {T('try_manually')} {BOLD}cd {WORKSPACE} && uv sync{RESET}")
        print(f"    {T('log_at')} {DIM}logs/uv-sync.log{RESET}")

    # Dashboard build
    frontend_dir = WORKSPACE / "dashboard" / "frontend"
    if (frontend_dir / "package.json").exists():
        print(f"  {DIM}{T('installing_dashboard_deps')}{RESET}", end="", flush=True)
        ret_install = os.system(f"cd {frontend_dir} && npm install --silent 2>{WORKSPACE}/logs/npm-install.log")
        if ret_install != 0:
            print(f"\r  {RED}✗{RESET} {T('dashboard_deps_failed')}                    ")
            print(f"    {YELLOW}{T('try_manually')} {BOLD}cd dashboard/frontend && npm install{RESET}")
            print(f"    {T('log_at')} {DIM}logs/npm-install.log{RESET}")
        else:
            print(f"\r  {GREEN}✓{RESET} {T('installed_dashboard_deps')}                    ")
            print(f"  {DIM}{T('building_dashboard')}{RESET}", end="", flush=True)
            ret_build = os.system(f"cd {frontend_dir} && npm run build 2>{WORKSPACE}/logs/npm-build.log 1>/dev/null")
            if ret_build != 0:
                print(f"\r  {RED}✗{RESET} {T('dashboard_build_failed')}                    ")
                print(f"    {YELLOW}{T('try_manually')} {BOLD}cd dashboard/frontend && npm run build{RESET}")
                print(f"    {T('log_at')} {DIM}logs/npm-build.log{RESET}")
            else:
                print(f"\r  {GREEN}✓{RESET} {T('built_dashboard')}                    ")

    # Terminal-server dependencies (always needed)
    ts_dir = WORKSPACE / "dashboard" / "terminal-server"
    if (ts_dir / "package.json").exists():
        print(f"  {DIM}{T('installing_terminal_deps')}{RESET}", end="", flush=True)
        ret = os.system(f"cd {ts_dir} && npm install --silent 2>{WORKSPACE}/logs/ts-install.log")
        if ret == 0:
            print(f"\r  {GREEN}✓{RESET} {T('installed_terminal_deps')}                    ")
        else:
            print(f"\r  {RED}✗{RESET} {T('terminal_deps_failed')}                    ")
            print(f"    {T('log_at')} {DIM}logs/ts-install.log{RESET}")

    # Data dir for SQLite
    (WORKSPACE / "dashboard" / "data").mkdir(parents=True, exist_ok=True)

    # Determine the service user.
    # Priority: SUDO_USER (ran with sudo) > create 'evonexus' user (root on VPS) > current user
    sudo_user = os.environ.get("SUDO_USER", "")
    service_user = sudo_user  # may be empty

    if os.getuid() == 0 and not sudo_user and is_remote:
        # Running as root directly (common on VPS) — create dedicated user
        service_user = "evonexus"
        print(f"\n  {DIM}Creating dedicated service user '{service_user}'...{RESET}")
        ret = os.system(f"id {service_user} >/dev/null 2>&1")
        if ret != 0:
            os.system(f"useradd -m -s /bin/bash {service_user}")
            print(f"  {GREEN}✓{RESET} User '{service_user}' created")
        else:
            print(f"  {DIM}✓ User '{service_user}' already exists{RESET}")

        # Copy installation to user home
        service_home = f"/home/{service_user}"
        service_dir = f"{service_home}/evo-nexus"
        if str(WORKSPACE.resolve()) != service_dir:
            print(f"  {DIM}Copying installation to {service_dir}...{RESET}")
            os.system(f"rm -rf {service_dir}")
            os.system(f"cp -a {WORKSPACE} {service_dir}")
            print(f"  {GREEN}✓{RESET} Copied to {service_dir}")
        # Update WORKSPACE reference for start-services.sh
        install_dir = Path(service_dir)

        # Install uv + claude-code for the service user
        ret = os.system(f"su - {service_user} -c 'command -v uv' >/dev/null 2>&1")
        if ret != 0:
            print(f"  {DIM}Installing uv for {service_user}...{RESET}")
            os.system(f"su - {service_user} -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null 2>&1")
            print(f"  {GREEN}✓{RESET} uv installed")

        ret = os.system(f"su - {service_user} -c 'export PATH=$HOME/.local/bin:$PATH && command -v claude' >/dev/null 2>&1")
        if ret != 0:
            print(f"  {DIM}Installing Claude Code for {service_user}...{RESET}")
            os.system(f"su - {service_user} -c 'npm install -g @anthropic-ai/claude-code --prefix ~/.local' >/dev/null 2>&1")
            print(f"  {GREEN}✓{RESET} Claude Code installed")

        # OpenClaude is required for non-Anthropic providers (OpenAI, Codex OAuth,
        # OpenRouter, Gemini, etc.). Without it, switching provider in the
        # dashboard does not work for the service user.
        # Check if installed AND on a new-enough version (0.3.0+); otherwise (re)install.
        oc_version = subprocess.run(
            ["su", "-", service_user, "-c", "export PATH=$HOME/.local/bin:$PATH && openclaude --version 2>/dev/null || true"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        oc_parsed = _parse_semver(oc_version)
        oc_ok = oc_parsed is not None and oc_parsed >= (0, 3, 0)
        if not oc_ok:
            if oc_parsed is not None:
                print(f"  {DIM}Upgrading OpenClaude for {service_user} (found {oc_version}, need 0.3.0+)...{RESET}")
            else:
                print(f"  {DIM}Installing OpenClaude for {service_user}...{RESET}")
            os.system(f"su - {service_user} -c 'npm install -g @gitlawb/openclaude@latest --prefix ~/.local' >/dev/null 2>&1")
            print(f"  {GREEN}✓{RESET} OpenClaude installed")

        # Sync deps as service user
        print(f"  {DIM}Syncing dependencies as {service_user}...{RESET}")
        os.system(f"su - {service_user} -c 'export PATH=$HOME/.local/bin:$PATH && cd {service_dir} && uv sync -q' 2>/dev/null")

        # Fix ownership
        os.system(f"chown -R {service_user}:{service_user} {service_dir}")
        os.system(f"chown -R {service_user}:{service_user} {service_home}")
    else:
        install_dir = WORKSPACE

    # Fix ownership BEFORE starting services.
    if service_user and os.getuid() == 0:
        target_dir = install_dir if service_user == "evonexus" else WORKSPACE
        print(f"  {DIM}{T('fixing_ownership', user=service_user)}{RESET}")
        os.system(f"chown -R {service_user}:{service_user} {target_dir}")
        os.system(f"chmod -R u+x {target_dir}/.venv/bin/ 2>/dev/null")
        print(f"  {GREEN}✓{RESET} {T('ownership_fixed')}")

    # Start dashboard services
    logs_dir = install_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    if service_user and os.getuid() == 0:
        os.system(f"chown -R {service_user}:{service_user} {logs_dir}")

    print(f"\n  {DIM}{T('starting_dashboard_services')}{RESET}")
    # Stop any existing services
    os.system("systemctl stop evo-nexus 2>/dev/null")
    os.system("pkill -f 'terminal-server/bin/server.js' 2>/dev/null")
    os.system("pkill -f 'app.py' 2>/dev/null")
    os.system("sleep 1")

    # The start-services.sh shipped in git (since #27) is
    # self-discovering — resolves SCRIPT_DIR via
    # $(dirname "${BASH_SOURCE[0]}") at runtime — and already includes
    # the scheduler launch. So it works regardless of install path or
    # service user. No regeneration needed; just ensure it's
    # executable.
    #
    # Until this commit, the block here REWROTE the file with a
    # hardcoded ``cd {install_dir}`` AND silently dropped the
    # ``scheduler.py`` launch line entirely. Net effect: the scheduler
    # never came up after a wizard install (cron-style routines,
    # integration sync, briefings — all dead) and the
    # self-discovering version from #27 was clobbered on every
    # ``make setup`` run.
    startup_script = install_dir / "start-services.sh"
    if startup_script.exists():
        os.chmod(startup_script, 0o755)

    # Create systemd service (remote/VPS only, when we have a service user)
    if is_remote and service_user and os.getuid() == 0:
        _setup_systemd_service(service_user, install_dir, logs_dir)
    elif service_user:
        print(f"  {DIM}(services will run as {service_user}){RESET}")
        os.system(f"su - {service_user} -c '{startup_script}'")
    else:
        os.system(str(startup_script))

    import time as _time
    _time.sleep(3)
    # Verify
    import urllib.request as _urllib
    try:
        _urllib.urlopen("http://localhost:32352", timeout=3)
        print(f"  {GREEN}✓{RESET} {T('terminal_started')}")
    except Exception:
        print(f"  {YELLOW}!{RESET} {T('terminal_not_started')}")
    try:
        _urllib.urlopen("http://localhost:8080", timeout=3)
        print(f"  {GREEN}✓{RESET} {T('dashboard_started')}")
    except Exception:
        print(f"  {YELLOW}!{RESET} {T('dashboard_not_started')}")

    dashboard_url = access_config.get('url', f'http://localhost:{dashboard_port}')

    if is_remote:
        svc_msg = ""
        if service_user == "evonexus":
            svc_msg = f"""
  {T('systemd_section')}
    {DIM}systemctl status evo-nexus{RESET}     — {T('systemd_status')}
    {DIM}systemctl restart evo-nexus{RESET}    — {T('systemd_restart')}
    {DIM}journalctl -u evo-nexus -f{RESET}     — {T('systemd_logs')}
    {DIM}su - evonexus{RESET}                  — {T('systemd_su')}
"""
        print(f"""
  {GREEN}{'='*50}{RESET}
  {GREEN}{T('setup_done')}{RESET}
  {GREEN}{'='*50}{RESET}

  {T('dashboard_available_at')}

    {BOLD}{dashboard_url}{RESET}

  {T('next_steps_header')}
    {T('next_step_1_remote')}
    {T('next_step_2_remote')}
    {T('next_step_3_remote')}
{svc_msg}""")
    else:
        print(f"""
  {GREEN}{T('setup_done')}{RESET}

  {T('next_steps_header')}
  {T('next_step_1_local')}
  {T('next_step_2_local')}
  {T('next_step_3_local', url=dashboard_url)}
""")


if __name__ == "__main__":
    # When invoked as a setuptools/pip build backend (via `pip install -e .`
    # or the `npx @evoapi/evo-nexus` CLI, which sets EVO_NEXUS_INSTALL=1),
    # we must NOT run the interactive wizard — there is no TTY, and input()
    # would raise EOFError. Instead expose proper package metadata and let
    # pip/setuptools do its job.
    #
    # This block improves on upstream PR #11 in two ways:
    #   1. Version comes from pyproject.toml (single source of truth),
    #      not a hardcoded string that drifts across releases.
    #   2. find_packages() discovers real packages, so any wheel built
    #      this way actually contains code (PR #11 shipped packages=[]).
    if _IS_BUILD_BACKEND:
        from setuptools import setup as _setup, find_packages
        _setup(
            name="evo-nexus",
            version=_read_version_from_pyproject(),
            description="Unofficial open source toolkit for Claude Code — AI-powered business operating system",
            packages=find_packages(exclude=("tests", "tests.*", "workspace", "workspace.*")),
            package_dir={"": "."},
            include_package_data=True,
            python_requires=">=3.10",
        )
    else:
        main()
