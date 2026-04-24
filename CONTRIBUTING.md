# Contributing to EvoNexus

Thank you for your interest in contributing to EvoNexus! This document provides guidelines for contributing.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/EvolutionAPI/evo-nexus/issues) to report bugs or request features
- Include steps to reproduce, expected behavior, and actual behavior
- Include your OS, Python version, and Node.js version

### Cloning the repository

The repository history currently carries legacy PNG avatar blobs that inflate
a full clone to roughly 290 MB even though the working tree is only ~13 MB.
Until the one-time history rewrite tracked in [#26](https://github.com/EvolutionAPI/evo-nexus/issues/26)
lands, we recommend cloning with blob filtering — git fetches objects on
demand instead of downloading the entire history up front:

```bash
# Recommended — ~10 MB, full log/blame still work, blobs lazy-load
git clone --filter=blob:none https://github.com/EvolutionAPI/evo-nexus.git

# Alternative if you only need the working tree (no history) — ~8 MB
git clone --depth 1 --branch develop https://github.com/EvolutionAPI/evo-nexus.git
```

Both forms support all normal read operations. `--filter=blob:none` is
preferred for regular contribution work because `git log`, `git blame` and
`git show` still function; the first time you access an older blob git
fetches it on demand.

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test locally (`make setup` + `make dashboard-app`)
5. Commit with a clear message
6. Push and open a PR

### What to Contribute

**High impact areas:**
- New agents for business domains (HR, Legal, Customer Success, Product)
- New integration clients (Slack, Outlook, HubSpot, QuickBooks)
- New skills for existing agents
- Dashboard improvements (new pages, charts, features)
- Documentation and guides
- Bug fixes
- Test coverage

### Creating a New Agent

1. Create `.claude/agents/my-agent.md` — follow the pattern in existing agents
2. Create `.claude/commands/my-agent.md` — slash command to invoke it
3. Add skills in `.claude/skills/prefix-*/SKILL.md`
4. Add routines in `ADWs/routines/custom/my_routine.py` (custom routines are gitignored — only core routines live in `ADWs/routines/`)
5. Add HTML template in `.claude/templates/html/`
6. Update `config/routines.yaml.example` with the new routine

### Creating a New Skill

1. Create `.claude/skills/prefix-name/SKILL.md`
2. Follow the YAML frontmatter format (name, description)
3. Keep SKILL.md under 500 lines
4. Add examples if helpful

### Creating a New Integration

1. Create script in `.claude/skills/int-name/scripts/client.py`
2. Create `.claude/skills/int-name/SKILL.md`
3. Add required env vars to `.env.example`
4. Document in the skill's SKILL.md

## Code Style

- **Python**: Follow PEP 8, use type hints where helpful
- **TypeScript/React**: Follow existing patterns in `dashboard/frontend/`
- **Markdown**: Use clear headers, keep skills concise
- **Commits**: Use conventional commits (`feat:`, `fix:`, `docs:`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
