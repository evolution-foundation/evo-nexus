#!/usr/bin/env python3
"""
EvoNexus — Setup Wizard
Generates workspace configuration, CLAUDE.md, .env, and folder structure.
Usage: python setup.py  (or: make setup)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent

# Detect if running in interactive terminal (vs pip/automated context)
IS_TTY = sys.stdin.isatty()

# Detect if running as pip/setuptools build backend (not interactive setup)
# pip sets these env vars when invoking setup.py as a build backend
_IS_BUILD_BACKEND = (
    "SETUPTOOLS_USE_DISTUTILS" in os.environ
    or "PIP_REQUIREMENTS_GEN" in os.environ
    or os.environ.get("EVO_NEXUS_INSTALL") == "1"
    or any(arg in sys.argv for arg in ["--with-specifier-add", "egg_info", "--version", "dist_info", "--editable"])
)

# ANSI colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def banner():
    print(f"""
{GREEN}  ╔══════════════════════════════════╗
  ║   {BOLD}EvoNexus — Setup Wizard{RESET}{GREEN}     ║
  ╚══════════════════════════════════╝{RESET}
""")


def _check_tool(name, cmd, install_cmd=None, install_label=None):
    """Check if a tool is installed. If not, offer to install it."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip()
            print(f"  {GREEN}✓{RESET} {name}: {DIM}{version}{RESET}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if install_cmd:
        print(f"  {YELLOW}!{RESET} {name} not found")
        # Only ask for interactive input if stdin is a TTY
        if IS_TTY:
            choice = input(f"    Install {name}? (Y/n): ").strip().lower()
        else:
            # Non-interactive (pip/automated): skip install attempt, mark as missing
            print(f"    {DIM}Skipping auto-install in non-interactive mode{RESET}")
            choice = "n"
        if choice in ("", "y", "yes", "s", "sim"):
            print(f"  {DIM}Installing {name}...{RESET}", end="", flush=True)
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
            print(f"\r  {RED}✗{RESET} Failed to install {name}                    ")
        else:
            print(f"  {RED}✗{RESET} {name} is required for EvoNexus")
    else:
        print(f"  {RED}✗{RESET} {name} not found — {install_label or 'install manually'}")

    return False


def check_prerequisites():
    """Check and auto-install required tools."""
    # Update system packages first (ensures fresh package lists)
    if os.getuid() == 0:
        print(f"  {DIM}Updating system packages...{RESET}", end="", flush=True)
        os.system("DEBIAN_FRONTEND=noninteractive apt-get update -y -qq > /dev/null 2>&1")
        os.system("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' > /dev/null 2>&1")
        print(f"\r  {GREEN}✓{RESET} System packages updated       ")

    missing = []

    # build-essential (required for native npm packages like node-pty)
    try:
        result = subprocess.run(["g++", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"  {GREEN}✓{RESET} build-essential: {DIM}installed{RESET}")
        else:
            raise FileNotFoundError
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(f"  {DIM}Installing build-essential...{RESET}", end="", flush=True)
        os.system("apt install -y build-essential > /dev/null 2>&1 || yum groupinstall -y 'Development Tools' > /dev/null 2>&1")
        try:
            result = subprocess.run(["g++", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"  {GREEN}✓{RESET} build-essential: {DIM}installed{RESET}")
            else:
                print(f"  {RED}✗{RESET} build-essential install failed")
                missing.append("build-essential")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"  {RED}✗{RESET} build-essential install failed")
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
        print(f"  {RED}✗{RESET} npm not found (should come with Node.js)")
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
    if not _check_tool("OpenClaude", ["openclaude", "--version"],
                        install_cmd="npm install -g @gitlawb/openclaude"):
        missing.append("openclaude")

    print()

    if missing:
        print(f"  {RED}The following tools could not be installed:{RESET}")
        for m in missing:
            print(f"    {RED}•{RESET} {m}")
        print(f"\n  {YELLOW}Install them manually and run setup again.{RESET}")
        sys.exit(1)

    return True


def configure_access() -> dict:
    """Configure how the dashboard will be accessed (local or domain with SSL)."""
    print(f"\n  {BOLD}Dashboard Access{RESET}\n")
    print(f"    {BOLD}1{RESET}) Local only (http://localhost:8080)")
    print(f"    {BOLD}2{RESET}) Domain with SSL (recommended for remote servers)")

    choice = ask("Type 1 or 2", "1")

    if choice not in ("1", "2"):
        print(f"  {YELLOW}!{RESET} Invalid choice '{choice}'. Using local mode.")
        return {"mode": "local", "url": "http://localhost:8080"}

    if choice == "1":
        return {"mode": "local", "url": "http://localhost:8080"}

    domain = ask("Domain (e.g. nexus.example.com)", "")
    if not domain:
        print(f"  {YELLOW}!{RESET} No domain provided, using local mode")
        return {"mode": "local", "url": "http://localhost:8080"}
    # Clean up if user pasted a full URL
    domain = domain.strip().replace("http://", "").replace("https://", "").rstrip("/")

    # Step 1: Install nginx
    if not shutil.which("nginx"):
        print(f"  {DIM}Installing nginx...{RESET}", end="", flush=True)
        os.system("apt install -y nginx > /dev/null 2>&1 || yum install -y nginx > /dev/null 2>&1")
        if not shutil.which("nginx"):
            print(f"  {RED}✗{RESET} nginx installation failed, using local mode")
            return {"mode": "local", "url": "http://localhost:8080"}
    print(f"  {GREEN}✓{RESET} nginx installed")

    # Step 2: Stop nginx to free port 80 for certbot
    os.system("systemctl stop nginx 2>/dev/null")

    # Step 3: SSL certificate — certbot by default, fallback to self-signed
    ssl_cert = ""
    ssl_key = ""

    ssl_mode = ask("SSL certificate (1=certbot, 2=self-signed, 3=manual path)", "1")

    if ssl_mode == "1":
        certbot_cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        certbot_key = f"/etc/letsencrypt/live/{domain}/privkey.pem"

        # Reuse existing certbot cert if found
        if os.path.isfile(certbot_cert) and os.path.isfile(certbot_key):
            ssl_cert = certbot_cert
            ssl_key = certbot_key
            print(f"  {GREEN}✓{RESET} Existing certbot certificate found for {domain}")
        else:
            # Install certbot if needed
            if not shutil.which("certbot"):
                print(f"  {DIM}Installing certbot...{RESET}", end="", flush=True)
                os.system("apt install -y certbot > /dev/null 2>&1")
                print(f"\r  {GREEN}✓{RESET} certbot installed                    ")
            # Obtain certificate (requires domain DNS pointing to this server)
            print(f"  {DIM}Obtaining SSL certificate via certbot...{RESET}", end="", flush=True)
            ret = os.system(f"certbot certonly --standalone -d {domain} --non-interactive --agree-tos --register-unsafely-without-email > /dev/null 2>&1")
            if ret == 0:
                ssl_cert = certbot_cert
                ssl_key = certbot_key
                print(f"\r  {GREEN}✓{RESET} SSL certificate obtained via certbot                    ")
            else:
                print(f"\r  {YELLOW}!{RESET} certbot failed — falling back to self-signed                    ")
                ssl_mode = "2"

    if ssl_mode == "2":
        # Self-signed (works with Cloudflare Full mode)
        print(f"  {DIM}Generating self-signed SSL certificate...{RESET}")
        os.system("mkdir -p /etc/nginx/ssl")
        ret = os.system(f'openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -keyout /etc/nginx/ssl/{domain}.key -out /etc/nginx/ssl/{domain}.crt -subj "/CN={domain}" 2>/dev/null')
        if ret == 0:
            ssl_cert = f"/etc/nginx/ssl/{domain}.crt"
            ssl_key = f"/etc/nginx/ssl/{domain}.key"
            print(f"  {GREEN}✓{RESET} Self-signed SSL certificate generated")
            print(f"  {DIM}(Compatible with Cloudflare SSL mode: Full){RESET}")
        else:
            print(f"  {RED}✗{RESET} Failed to generate SSL certificate")

    if ssl_mode == "3":
        ssl_cert = ask("SSL cert path", f"/etc/nginx/ssl/{domain}.crt")
        ssl_key = ask("SSL key path", f"/etc/nginx/ssl/{domain}.key")

    # Fix SSL key permissions (nginx needs read access, restrict from others)
    if ssl_key and os.path.isfile(ssl_key):
        os.chmod(ssl_key, 0o600)

    if not ssl_cert or not ssl_key:
        print(f"  {RED}✗{RESET} No SSL certificate available, using local mode")
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
                print(f"  {GREEN}✓{RESET} Removed nginx default site")

        nginx_path = "/etc/nginx/sites-enabled/evonexus"
        with open(nginx_path, "w") as f:
            f.write(nginx_config)

        # Test nginx config
        ret = os.system("nginx -t 2>/tmp/nginx-test.log")
        if ret == 0:
            os.system("systemctl reload nginx 2>/dev/null || systemctl start nginx 2>/dev/null")
            os.system("systemctl enable nginx 2>/dev/null")
            print(f"  {GREEN}✓{RESET} Nginx configured for {domain}")
        else:
            # nginx -t failed — likely SSL cert issue. Show the error clearly.
            print(f"  {RED}✗{RESET} Nginx config test failed")
            os.system("cat /tmp/nginx-test.log 2>/dev/null")
            print(f"    {YELLOW}The config is saved at {nginx_path}{RESET}")
            print(f"    {YELLOW}Fix the issue and run: nginx -t && systemctl reload nginx{RESET}")

        # Verify the config file actually exists after writing
        if not os.path.exists(nginx_path):
            print(f"  {RED}✗{RESET} Nginx config file was not created at {nginx_path}")
    except PermissionError:
        print(f"  {YELLOW}!{RESET} No permission to write nginx config — run setup as root/sudo")

    # Step 5: Open firewall ports
    print(f"  {DIM}Configuring firewall...{RESET}")
    os.system("ufw allow 80/tcp 2>/dev/null; ufw allow 443/tcp 2>/dev/null; ufw allow 8080/tcp 2>/dev/null; ufw allow 32352/tcp 2>/dev/null")
    os.system("iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null; iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null")
    print(f"  {GREEN}✓{RESET} Firewall ports opened (80, 443)")

    return {"mode": "domain", "url": f"https://{domain}"}


def choose_provider() -> str:
    """Ask the user which AI provider to use."""
    print(f"""
  Choose your AI provider:

    {BOLD}1{RESET}) Anthropic (Claude nativo)         — default, no extra config
    {BOLD}2{RESET}) OpenRouter (200+ models)           — requires API key + openclaude
    {BOLD}3{RESET}) OpenAI (GPT-4.x / GPT-5.x)         — API key or OAuth + openclaude
    {BOLD}4{RESET}) Google Gemini                      — coming soon
    {BOLD}5{RESET}) AWS Bedrock                        — coming soon
    {BOLD}6{RESET}) Google Vertex AI                   — coming soon
""")
    choice = ask("Provider (1-3)", "1")
    provider_map = {
        "1": "anthropic", "2": "openrouter", "3": "openai",
    }
    if choice in ("4", "5", "6"):
        print(f"  {YELLOW}!{RESET} This provider is coming soon. Using Anthropic for now.")
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
            print(f"  {YELLOW}!{RESET} openclaude not found — needed for {provider_id}")
            print(f"    {DIM}npm install -g @gitlawb/openclaude{RESET}")
            install = ask("Install now? (y/n)", "y")
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
                "openai": {"name": "OpenAI", "description": "GPT-4.x via API Key ou GPT-5.x via Codex OAuth", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""}, "default_model": "gpt-4.1", "requires_logout": True},
                "gemini": {"name": "Google Gemini (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_GEMINI": "1", "GEMINI_API_KEY": "", "GEMINI_MODEL": ""}, "default_model": "gemini-2.5-pro", "requires_logout": True, "coming_soon": True},
                "bedrock": {"name": "AWS Bedrock (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "", "AWS_BEARER_TOKEN_BEDROCK": ""}, "requires_logout": True, "coming_soon": True},
                "vertex": {"name": "Google Vertex AI (em breve)", "cli_command": "openclaude", "env_vars": {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "", "CLOUD_ML_REGION": ""}, "default_region": "us-east5", "requires_logout": True, "coming_soon": True},
            }
        }

    # Collect env vars for the chosen provider
    prov = config["providers"].get(provider_id, {})
    env_vars = prov.get("env_vars", {})

    if provider_id == "openai":
        print(f"\n  {BOLD}OpenAI Authentication{RESET}")
        print(f"    {BOLD}a{RESET}) API Key (GPT-4.x)")
        print(f"    {BOLD}b{RESET}) Codex OAuth (GPT-5.x) — via Dashboard")
        auth_choice = ask("Auth method (a/b)", "b")

        if auth_choice.lower() == "a":
            api_key = ask("  OPENAI_API_KEY", "")
            model = ask("  OPENAI_MODEL", prov.get("default_model", "gpt-4.1"))
            env_vars = {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_API_KEY": api_key, "OPENAI_MODEL": model}
        else:
            model = ask("  OPENAI_MODEL", "gpt-5.4")
            env_vars = {"CLAUDE_CODE_USE_OPENAI": "1", "OPENAI_MODEL": model}
            print(f"\n  {GREEN}✓{RESET} Provider configurado: OpenAI (Codex OAuth)")
            print(f"  {YELLOW}!{RESET} Para completar a autenticacao, acesse o Dashboard")
            print(f"    {BOLD}Providers → Login com OpenAI{RESET}")

        prov["env_vars"] = env_vars

    elif provider_id != "anthropic":
        print(f"\n  {BOLD}Configure {prov.get('name', provider_id)}{RESET}")
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
    print(f"  {GREEN}✓{RESET} Saved provider config: {provider_id}")

    if prov.get("requires_logout"):
        print(f"  {YELLOW}!{RESET} Remember to run /logout in Claude Code if previously logged into Anthropic")

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

    print(f"\n  {DIM}Enter keys to toggle (comma-separated), or press Enter to accept:{RESET}")
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
        print(f"  {YELLOW}!{RESET} .env already exists, skipping")
        return
    if src.exists():
        shutil.copy2(src, dst)
        print(f"  {GREEN}✓{RESET} Created .env from .env.example")
    else:
        print(f"  {YELLOW}!{RESET} .env.example not found, creating empty .env")
        dst.write_text("# EvoNexus Environment Variables\n# Fill in your API keys below\n\n", encoding="utf-8")


def copy_routines_config(config: dict):
    dst = WORKSPACE / "config" / "routines.yaml"
    if dst.exists():
        print(f"  {YELLOW}!{RESET} config/routines.yaml already exists, skipping")
        return
    # Try example file first, otherwise generate minimal config
    src = WORKSPACE / "config" / "routines.yaml.example"
    if src.exists():
        shutil.copy2(src, dst)
    else:
        dst.write_text("# EvoNexus Routines — edit schedules here\n# See ROUTINES.md for documentation\n\ndaily: []\nweekly: []\nmonthly: []\n", encoding="utf-8")
    print(f"  {GREEN}✓{RESET} Created config/routines.yaml")


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

    print(f"  {GREEN}✓{RESET} Created workspace folders ({count})")


def _setup_systemd_service(service_user, install_dir, logs_dir):
    """Create and start a systemd service for EvoNexus."""
    service_home = f"/home/{service_user}"
    service_name = "evo-nexus"
    service_file = f"/etc/systemd/system/{service_name}.service"

    print(f"  {DIM}Creating systemd service...{RESET}")

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
    print(f"  {GREEN}✓{RESET} Systemd service created and enabled (auto-starts on boot)")
    print(f"  {DIM}  Manage with: systemctl {{start|stop|restart|status}} {service_name}{RESET}")


def main():
    # If running as pip build backend, just return (pip handles deps via pyproject.toml)
    if _IS_BUILD_BACKEND:
        return

    banner()

    # Prerequisites check
    print(f"  {BOLD}Checking prerequisites...{RESET}")
    check_prerequisites()

    # Dashboard access (Nginx config) — FIRST question
    access_config = configure_access()
    is_remote = access_config.get("mode") == "domain"

    if is_remote:
        # Remote mode: minimal setup, then redirect to dashboard
        print(f"\n  {BOLD}Quick setup for remote access...{RESET}")
        owner_name = ""
        company_name = ""
        timezone = "America/Sao_Paulo"
        language = "ptBR"
        dashboard_port = 8080
    else:
        # Local mode: full interactive setup
        # Provider choice
        print(f"  {BOLD}AI Provider{RESET}")
        provider_choice = choose_provider()
        print()

        # Who are you?
        print(f"  {BOLD}About you{RESET}")
        owner_name = ask("Your name", "")
        company_name = ask("Company name", "")
        timezone = ask("Timezone", "America/Sao_Paulo")
        language = ask("Language", "ptBR")
        dashboard_port = int(ask("Dashboard port", "8080"))
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

    print(f"  {BOLD}Creating workspace...{RESET}")

    # workspace.yaml
    config_dir = WORKSPACE / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "workspace.yaml").write_text(generate_workspace_yaml(config), encoding="utf-8")
    print(f"  {GREEN}✓{RESET} Generated config/workspace.yaml")

    # .env
    copy_env_example(config)

    # routines.yaml
    copy_routines_config(config)

    # CLAUDE.md
    claude_md = generate_claude_md(config)
    (WORKSPACE / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    print(f"  {GREEN}✓{RESET} Generated CLAUDE.md")

    # Folders
    create_folders(config)

    # Logs dir (for install logs)
    (WORKSPACE / "logs").mkdir(exist_ok=True)

    # Install Python dependencies
    # Must run as the ORIGINAL user (not root) so .venv symlinks
    # point to user's Python, not /root/.local/share/uv/python/
    print(f"  {DIM}Installing Python dependencies...{RESET}", end="", flush=True)
    _sudo_user = os.environ.get("SUDO_USER", "")
    if _sudo_user and os.getuid() == 0:
        ret = os.system(f"su - {_sudo_user} -c 'cd {WORKSPACE} && uv sync -q' 2>{WORKSPACE}/logs/uv-sync.log")
    else:
        ret = os.system(f"cd {WORKSPACE} && uv sync -q 2>{WORKSPACE}/logs/uv-sync.log")
    if ret == 0 and (WORKSPACE / ".venv" / "bin" / "python").exists():
        print(f"\r  {GREEN}✓{RESET} Installed Python dependencies                    ")
    else:
        print(f"\r  {RED}✗{RESET} Python dependencies failed to install                    ")
        print(f"    {YELLOW}This is needed for the dashboard to work.{RESET}")
        print(f"    Try running manually: {BOLD}cd {WORKSPACE} && uv sync{RESET}")
        print(f"    Log: {DIM}logs/uv-sync.log{RESET}")

    # Dashboard build
    frontend_dir = WORKSPACE / "dashboard" / "frontend"
    if (frontend_dir / "package.json").exists():
        print(f"  {DIM}Installing dashboard dependencies...{RESET}", end="", flush=True)
        ret_install = os.system(f"cd {frontend_dir} && npm install --silent 2>{WORKSPACE}/logs/npm-install.log")
        if ret_install != 0:
            print(f"\r  {RED}✗{RESET} Dashboard dependencies failed                    ")
            print(f"    {YELLOW}Try running manually: {BOLD}cd dashboard/frontend && npm install{RESET}")
            print(f"    Log: {DIM}logs/npm-install.log{RESET}")
        else:
            print(f"\r  {GREEN}✓{RESET} Installed dashboard dependencies                    ")
            print(f"  {DIM}Building dashboard frontend...{RESET}", end="", flush=True)
            ret_build = os.system(f"cd {frontend_dir} && npm run build 2>{WORKSPACE}/logs/npm-build.log 1>/dev/null")
            if ret_build != 0:
                print(f"\r  {RED}✗{RESET} Dashboard build failed                    ")
                print(f"    {YELLOW}Try running manually: {BOLD}cd dashboard/frontend && npm run build{RESET}")
                print(f"    Log: {DIM}logs/npm-build.log{RESET}")
            else:
                print(f"\r  {GREEN}✓{RESET} Built dashboard frontend                    ")

    # Terminal-server dependencies (always needed)
    ts_dir = WORKSPACE / "dashboard" / "terminal-server"
    if (ts_dir / "package.json").exists():
        print(f"  {DIM}Installing terminal-server dependencies...{RESET}", end="", flush=True)
        ret = os.system(f"cd {ts_dir} && npm install --silent 2>{WORKSPACE}/logs/ts-install.log")
        if ret == 0:
            print(f"\r  {GREEN}✓{RESET} Installed terminal-server dependencies                    ")
        else:
            print(f"\r  {RED}✗{RESET} Terminal-server dependencies failed                    ")
            print(f"    Log: {DIM}logs/ts-install.log{RESET}")

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
        print(f"  {DIM}Fixing file ownership for {service_user}...{RESET}")
        os.system(f"chown -R {service_user}:{service_user} {target_dir}")
        os.system(f"chmod -R u+x {target_dir}/.venv/bin/ 2>/dev/null")
        print(f"  {GREEN}✓{RESET} Ownership fixed")

    # Start dashboard services
    logs_dir = install_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    if service_user and os.getuid() == 0:
        os.system(f"chown -R {service_user}:{service_user} {logs_dir}")

    print(f"\n  {DIM}Starting dashboard services...{RESET}")
    # Stop any existing services
    os.system("systemctl stop evo-nexus 2>/dev/null")
    os.system("pkill -f 'terminal-server/bin/server.js' 2>/dev/null")
    os.system("pkill -f 'app.py' 2>/dev/null")
    os.system("sleep 1")

    # Create start-services.sh
    startup_script = install_dir / "start-services.sh"
    startup_script.write_text(f"""#!/bin/bash
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"
cd {install_dir}

# Kill existing services
pkill -f 'terminal-server/bin/server.js' 2>/dev/null
pkill -f 'dashboard/backend.*app.py' 2>/dev/null
sleep 1

# Clean stale sessions — old sessions cause agent persona issues
rm -f $HOME/.claude-code-web/sessions.json 2>/dev/null

# Start terminal-server (must run FROM the project root for agent discovery)
nohup node dashboard/terminal-server/bin/server.js > {logs_dir}/terminal-server.log 2>&1 &

# Start Flask dashboard
cd dashboard/backend
nohup {install_dir}/.venv/bin/python app.py > {logs_dir}/dashboard.log 2>&1 &
""", encoding="utf-8")
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
        print(f"  {GREEN}✓{RESET} Terminal server started (port 32352)")
    except Exception:
        print(f"  {YELLOW}!{RESET} Terminal server may not have started — check logs/terminal-server.log")
    try:
        _urllib.urlopen("http://localhost:8080", timeout=3)
        print(f"  {GREEN}✓{RESET} Dashboard started (port 8080)")
    except Exception:
        print(f"  {YELLOW}!{RESET} Dashboard may not have started — check logs/dashboard.log")

    dashboard_url = access_config.get('url', f'http://localhost:{dashboard_port}')

    if is_remote:
        svc_msg = ""
        if service_user == "evonexus":
            svc_msg = f"""
  Servico systemd:
    {DIM}systemctl status evo-nexus{RESET}     — verificar status
    {DIM}systemctl restart evo-nexus{RESET}    — reiniciar
    {DIM}journalctl -u evo-nexus -f{RESET}     — ver logs
    {DIM}su - evonexus{RESET}                  — acessar usuario do servico
"""
        print(f"""
  {GREEN}{'='*50}{RESET}
  {GREEN}Setup concluido!{RESET}
  {GREEN}{'='*50}{RESET}

  Dashboard disponivel em:

    {BOLD}{dashboard_url}{RESET}

  Proximo passo:
    1. Acesse o link acima e crie sua conta de administrador
    2. Va em {BOLD}Providers{RESET} e configure o AI Provider
    3. Abra um agente e comece a usar!
{svc_msg}""")
    else:
        print(f"""
  {GREEN}Done!{RESET} Next steps:
  1. Edit {BOLD}.env{RESET} with your API keys
  2. Run: {BOLD}make dashboard-app{RESET}
  3. Open {BOLD}{dashboard_url}{RESET} to create your admin account
  4. Run: {BOLD}make scheduler{RESET}    — start automated routines
  5. Run: {BOLD}make help{RESET}         — see all commands

  {YELLOW}Note:{RESET} The admin account is created via the web dashboard,
  not via CLI.
""")


if __name__ == "__main__":
    # When pip/setuptools runs setup.py as build backend, use setup() for metadata
    # When called directly (python setup.py), run the interactive wizard
    if _IS_BUILD_BACKEND:
        from setuptools import setup as _setup
        _setup(
            name="evo-nexus",
            version="0.23.2",
            py_modules=[],
            package_dir={"": "."},
            packages=[],
        )
    else:
        main()
