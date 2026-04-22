# ============================================================
# EvoNexus — Makefile
# ============================================================
# Usage: make <command>
# Docs: ROUTINES.md
#
# Core routines have dedicated targets below.
# Custom routines (ADWs/routines/custom/) are user-specific —
# run them with: make run R=<id>  (e.g. make run R=fin-pulse)
# List all available: make list-routines

# Auto-detect: uv if available, fallback to python3
PYTHON := $(shell command -v uv >/dev/null 2>&1 && echo "uv run python" || echo "python3")
ADW_DIR := ADWs/routines

# Load .env if it exists
ifneq (,$(wildcard .env))
include .env
export
endif

# ── Setup ──────────────────────────────────

docs-build:         ## 📄 Regenerate docs/llms-full.txt and sync to site
	@$(PYTHON) -c "from pathlib import Path; docs=Path('docs'); parts=['# EvoNexus Documentation\n\nComplete reference.\n']; [parts.append(f.read_text()) for f in sorted(docs.rglob('*.md'))]; Path('docs/llms-full.txt').write_text('\n\n---\n\n'.join(parts)); print(f'Generated docs/llms-full.txt ({len(parts)-1} docs)')"
	@rm -rf site/public/docs && cp -r docs/ site/public/docs/ && echo "Synced docs → site/public/docs/"

setup:              ## 🔧 Interactive setup wizard (prerequisites, config, folders)
	$(PYTHON) setup.py

init-key:           ## 🔐 Generate KNOWLEDGE_MASTER_KEY for pgvector-knowledge (one-time)
	$(PYTHON) -m dashboard.backend.knowledge.cli init-key

# ── Core Routines (shipped with repo) ─────

morning:            ## ☀️  Morning briefing — agenda, emails, tasks (@clawdia)
	$(PYTHON) $(ADW_DIR)/good_morning.py

eod:                ## 🌙 End of day consolidation — memory, logs, learnings (@clawdia)
	$(PYTHON) $(ADW_DIR)/end_of_day.py

memory:             ## 🧠 Consolidate memory (@clawdia)
	$(PYTHON) $(ADW_DIR)/memory_sync.py

memory-lint:        ## 🔍 Memory health check — contradictions, gaps, stale data (@clawdia)
	$(PYTHON) $(ADW_DIR)/memory_lint.py

weekly:             ## 📊 Full weekly review (@clawdia)
	$(PYTHON) $(ADW_DIR)/weekly_review.py

backup-daily:       ## 💾 Daily backup routine (scheduled, systematic)
	$(PYTHON) $(ADW_DIR)/backup.py

learn-weekly:       ## 📚 Learning loop weekly report — overdue facts + stats (Sundays 09:45 BRT)
	$(PYTHON) $(ADW_DIR)/custom/learning_weekly.py

# ── Dynamic Routine Runner ────────────────
# Run any routine (core or custom) by its ID.
# IDs are derived from script names. Use `make list-routines` to see all.
# Examples:
#   make run R=morning
#   make run R=fin-pulse
#   make run R=community-week

run:                ## ▶️  Run any routine: make run R=<id>  (e.g. make run R=fin-pulse)
	@$(PYTHON) -c "import sys; sys.path.insert(0, 'dashboard/backend'); from routes._helpers import get_routine_scripts; scripts = get_routine_scripts(); r = '$(R)'; s = scripts.get(r) or next((v for k,v in scripts.items() if r.replace('-','_') in v), None); print(f'Running: {s}') if s else (print(f'Unknown routine: {r}'), exit(1))" && $(PYTHON) $(ADW_DIR)/$$($(PYTHON) -c "import sys; sys.path.insert(0, 'dashboard/backend'); from routes._helpers import get_routine_scripts; scripts = get_routine_scripts(); r = '$(R)'; s = scripts.get(r) or next((v for k,v in scripts.items() if r.replace('-','_') in v), ''); print(s)")

list-routines:      ## 📋 List all available routines (dynamic from scripts)
	@$(PYTHON) -c "import sys; sys.path.insert(0, 'dashboard/backend'); from routes._helpers import discover_routines; routines = discover_routines(); [print(f'  \033[36m{k:20s}\033[0m {v[\"name\"]:30s} @{v[\"agent\"]:<10s} {v[\"script\"]}') for k,v in sorted(routines.items())]; print(f'\n  {len(routines)} routines available — run with: make run R=<id>')"

# ── Agent Teams (experimental, opt-in) ───
# Parallel multi-agent versions of consolidation routines.
# Higher token cost (~3-5x), faster execution. Run manually when you want speed.

team-strategy:      ## 🧪 Strategy digest via agent team (parallel, experimental)
	@echo "⚠️  Agent Teams mode — expect higher token cost (~3-5x vs normal)"
	$(PYTHON) $(ADW_DIR)/custom/strategy_digest.py --team

team-dashboard:     ## 🧪 Dashboard via agent team (parallel, experimental)
	@echo "⚠️  Agent Teams mode — expect higher token cost (~3-5x vs normal)"
	$(PYTHON) $(ADW_DIR)/custom/dashboard.py --team

team-weekly:        ## 🧪 Weekly review via agent team (parallel, experimental)
	@echo "⚠️  Agent Teams mode — expect higher token cost (~3-5x vs normal)"
	$(PYTHON) $(ADW_DIR)/weekly_review.py --team

# ── Combos ────────────────────────────────

daily:              ## ☀️  Combo: sync meetings + review todoist
	@$(PYTHON) $(ADW_DIR)/custom/sync_meetings.py 2>/dev/null; $(PYTHON) $(ADW_DIR)/custom/review_todoist.py 2>/dev/null; echo "Daily combo done"

# ── Servers ───────────────────────────────

scheduler:          ## ⏰ Start routine scheduler (runs in background)
	$(PYTHON) scheduler.py

dashboard-app:      ## 🖥️  Start Dashboard App (React + Flask + terminal-server, localhost:8080)
	@cd dashboard/frontend && npm install --silent && npm run build
	@echo "▶ Starting terminal-server on :32352 (background)..."
	@pkill -f "[d]ashboard/terminal-server/bin/server.js" 2>/dev/null || true
	@node dashboard/terminal-server/bin/server.js --dev > /tmp/terminal-server.log 2>&1 & echo $$! > /tmp/terminal-server.pid
	@trap 'echo "▶ Stopping terminal-server..."; kill $$(cat /tmp/terminal-server.pid) 2>/dev/null; rm -f /tmp/terminal-server.pid' EXIT INT TERM; \
		cd dashboard/backend && $(PYTHON) app.py

terminal-logs:      ## 📜 Tail terminal-server logs
	@tail -f /tmp/terminal-server.log

terminal-stop:      ## 🛑 Stop terminal-server (if orphaned)
	@pkill -f "[d]ashboard/terminal-server/bin/server.js" 2>/dev/null && echo "✅ terminal-server stopped" || echo "ℹ terminal-server not running"
	@rm -f /tmp/terminal-server.pid

stop:               ## 🛑 Stop all EvoNexus services (dashboard + terminal-server)
	@echo "Stopping EvoNexus services..."
	@pkill -f "[d]ashboard/terminal-server/bin/server.js" 2>/dev/null || true
	@pkill -f "[d]ashboard/backend.*app.py" 2>/dev/null || true
	@pkill -f "[a]pp.py" 2>/dev/null || true
	@echo "✅ All services stopped"

uninstall:          ## 🗑️  Full cleanup — stop services, remove nginx, data, deps (DESTRUCTIVE)
	@echo ""
	@echo "⚠  This will STOP all services and DELETE:"
	@echo "   • dashboard/data/ (SQLite database)"
	@echo "   • dashboard/frontend/node_modules/"
	@echo "   • dashboard/terminal-server/node_modules/"
	@echo "   • .venv/ (Python virtual environment)"
	@echo "   • logs/"
	@echo "   • config/workspace.yaml, config/providers.json"
	@echo "   • /etc/nginx/sites-enabled/evonexus"
	@echo ""
	@read -p "  Type 'UNINSTALL' to confirm: " confirm; \
	if [ "$$confirm" = "UNINSTALL" ]; then \
		echo ""; \
		echo "Stopping services..."; \
		pkill -f "[d]ashboard/terminal-server/bin/server.js" 2>/dev/null || true; \
		pkill -f "[d]ashboard/backend.*app.py" 2>/dev/null || true; \
		pkill -f "[a]pp.py" 2>/dev/null || true; \
		echo "Removing nginx config..."; \
		rm -f /etc/nginx/sites-enabled/evonexus 2>/dev/null || true; \
		systemctl reload nginx 2>/dev/null || true; \
		echo "Removing generated files..."; \
		rm -rf dashboard/data/ dashboard/frontend/node_modules/ dashboard/frontend/dist/ dashboard/terminal-server/node_modules/ .venv/ logs/ start-services.sh; \
		rm -f config/workspace.yaml config/providers.json config/routines.yaml .env; \
		rm -f CLAUDE.md; \
		echo ""; \
		echo "✅ EvoNexus uninstalled. Run 'make setup' to reinstall."; \
	else \
		echo "Aborted."; \
	fi

bling-auth:         ## 🔐 Bling OAuth2 login (one-time: capture access + refresh tokens into .env)
	@python3 .claude/skills/int-bling/scripts/bling_auth.py

telegram:           ## 📨 Start Telegram bot in background (screen)
	@if screen -list | grep -q '\.telegram'; then \
		echo "⚠ Telegram bot is already running. Use 'make telegram-stop' first or 'make telegram-attach' to connect."; \
	else \
		screen -dmS telegram claude --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions; \
		echo "✅ Telegram bot running in background (screen: telegram)"; \
		echo "📺 Ver: screen -r telegram"; \
		echo "🛑 Parar: make telegram-stop"; \
	fi

telegram-stop:      ## 🛑 Stop the Telegram bot
	@screen -S telegram -X quit 2>/dev/null && echo "✅ Telegram bot stopped" || echo "⚠ Was not running"

telegram-attach:    ## 📺 Connect to Telegram terminal (Ctrl+A D to detach)
	@screen -r telegram

discord-channel:    ## 💬 Start Discord channel in background (screen)
	@if screen -list | grep -q '\.discord-channel'; then \
		echo "⚠ Discord channel is already running. Use 'make discord-channel-stop' first or 'make discord-channel-attach' to connect."; \
	else \
		screen -dmS discord-channel claude --channels plugin:discord@claude-plugins-official --dangerously-skip-permissions; \
		echo "✅ Discord channel running in background (screen: discord-channel)"; \
		echo "📺 Ver: screen -r discord-channel"; \
		echo "🛑 Parar: make discord-channel-stop"; \
	fi

discord-channel-stop: ## 🛑 Stop the Discord channel
	@screen -S discord-channel -X quit 2>/dev/null && echo "✅ Discord channel stopped" || echo "⚠ Was not running"

discord-channel-attach: ## 📺 Connect to Discord channel terminal (Ctrl+A D to detach)
	@screen -r discord-channel

imessage:           ## 💬 Start iMessage channel in background (screen)
	@if screen -list | grep -q '\.imessage'; then \
		echo "⚠ iMessage channel is already running. Use 'make imessage-stop' first or 'make imessage-attach' to connect."; \
	else \
		screen -dmS imessage claude --channels plugin:imessage@claude-plugins-official --dangerously-skip-permissions; \
		echo "✅ iMessage channel running in background (screen: imessage)"; \
		echo "📺 Ver: screen -r imessage"; \
		echo "🛑 Parar: make imessage-stop"; \
	fi

imessage-stop:      ## 🛑 Stop the iMessage channel
	@screen -S imessage -X quit 2>/dev/null && echo "✅ iMessage channel stopped" || echo "⚠ Was not running"

imessage-attach:    ## 📺 Connect to iMessage channel terminal (Ctrl+A D to detach)
	@screen -r imessage

# ── Utilities ─────────────────────────────

backup:             ## 💾 Backup workspace data (gitignored files) to local ZIP
	$(PYTHON) backup.py backup

backup-s3:          ## ☁️  Backup workspace data to local ZIP + S3 upload
	$(PYTHON) backup.py backup --target s3

restore:            ## 📥 Restore workspace from backup ZIP: make restore FILE=<path> [MODE=merge|replace]
	@echo "▶ Stopping services before restore..."
	@pkill -f "[d]ashboard/terminal-server/bin/server.js" 2>/dev/null || true
	@pkill -f "[a]pp.py" 2>/dev/null || true
	@sleep 1
	$(PYTHON) backup.py restore $(FILE) --mode $(or $(MODE),merge)
	@echo "▶ Restarting services..."
	@if [ -f start-services.sh ]; then bash start-services.sh; sleep 3; echo "✅ Services restarted"; else echo "ℹ Run ./start-services.sh or make dashboard-app to start"; fi

backup-list:        ## 📋 List available backups (local or S3: make backup-list TARGET=s3)
	$(PYTHON) backup.py list --target $(or $(TARGET),local)

logs:               ## 📝 Show latest logs (JSONL)
	@tail -20 ADWs/logs/$$(ls -t ADWs/logs/*.jsonl 2>/dev/null | head -1) 2>/dev/null || echo "No logs yet."

logs-detail:        ## 📝 List detailed logs
	@ls -lt ADWs/logs/detail/ 2>/dev/null | head -11 || echo "No logs yet."

logs-tail:          ## 📝 Show latest full log
	@cat ADWs/logs/detail/$$(ls -t ADWs/logs/detail/ 2>/dev/null | head -1) 2>/dev/null || echo "No logs yet."

metrics:            ## 📈 Show accumulated metrics per routine (tokens + cost)
	@python3 -c "\
	import json; d=json.load(open('ADWs/logs/metrics.json'));\
	total_runs=0; total_cost=0; total_tok=0;\
	[(\
	  print(f'  {k:22s} runs:{v[\"runs\"]:3d}  ok:{v[\"success_rate\"]:5.1f}%  avg:{v[\"avg_seconds\"]:5.0f}s  cost:\$${v.get(\"total_cost_usd\",0):7.2f}  avg:\$${v.get(\"avg_cost_usd\",0):.2f}  tok:{v.get(\"total_input_tokens\",0)+v.get(\"total_output_tokens\",0):>9,}  last:{v[\"last_run\"][:16]}'),\
	  total_runs:=total_runs+v['runs'],\
	  total_cost:=total_cost+v.get('total_cost_usd',0),\
	  total_tok:=total_tok+v.get('total_input_tokens',0)+v.get('total_output_tokens',0)\
	) for k,v in sorted(d.items())];\
	print(f'\n  {\"TOTAL\":22s} runs:{total_runs:3d}  {\" \":18s}  cost:\$${total_cost:7.2f}  {\" \":10s}  tok:{total_tok:>9,}')\
	" 2>/dev/null || echo "No metrics yet."

clean-logs:         ## 🗑️  Remove logs older than 30 days
	@find ADWs/logs/ -name "*.log" -mtime +30 -delete 2>/dev/null; find ADWs/logs/ -name "*.jsonl" -mtime +30 -delete 2>/dev/null; echo "Old logs removed."

# ── Docker (VPS) ──────────────────────────

docker-dashboard:   ## 🐳 Start dashboard in Docker (port 8080)
	docker compose up -d dashboard

docker-telegram:    ## 🐳 Start Telegram bot in Docker
	docker compose up -d telegram

docker-down:        ## 🐳 Stop all containers
	docker compose down

docker-logs:        ## 🐳 Container logs
	docker compose logs -f --tail=50

docker-run:         ## 🐳 Run routine manually (ex: make docker-run ADW=good_morning.py)
	docker compose run --rm runner ADWs/routines/$(ADW)

docker-build:       ## 🐳 Build the image
	docker compose build

heartbeat-lint:     ## 🔍 Validate config/heartbeats.yaml against pydantic schema
	@cd dashboard/backend && $(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from heartbeat_schema import load_heartbeats_yaml; \
from pathlib import Path; \
path = Path('../../config/heartbeats.yaml'); \
cfg = load_heartbeats_yaml(path); \
print(f'OK — {len(cfg.heartbeats)} heartbeat(s) validated'); \
[print(f'  {h.id}: agent={h.agent} interval={h.interval_seconds}s enabled={h.enabled}') for h in cfg.heartbeats]"

heartbeat-run:      ## ▶️  Run a heartbeat manually: make heartbeat-run ID=atlas-4h
	@cd dashboard/backend && $(PYTHON) heartbeat_runner.py --heartbeat-id $(ID)

help:               ## 📖 Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: morning eod memory memory-lint weekly run list-routines daily scheduler dashboard-app terminal-logs terminal-stop telegram telegram-stop telegram-attach discord-channel discord-channel-stop discord-channel-attach imessage imessage-stop imessage-attach backup backup-s3 restore backup-list backup-daily logs logs-detail logs-tail metrics clean-logs docker-dashboard docker-telegram docker-down docker-logs docker-run docker-build help docs-build setup team-strategy team-dashboard team-weekly learn-weekly heartbeat-lint heartbeat-run
.DEFAULT_GOAL := help
