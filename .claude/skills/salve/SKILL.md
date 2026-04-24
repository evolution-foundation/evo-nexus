---
name: salve
description: Flush session to memory — review what happened, update markdowns, create session log
---

# /salve

Quando invocado:

1. **Revisar a sessão atual** — identificar:
   - Decisões tomadas
   - Pendências criadas ou resolvidas
   - Pessoas mencionadas com novo contexto
   - Projetos com atualizações
   - Métricas ou números relevantes
   - Prazos e deadlines

2. **Atualizar arquivos de memória** (seguindo regras de propagação):
   - `memory/context/decisoes/YYYY-MM.md` — novas decisões
   - `memory/context/pendencias.md` — pendências abertas/fechadas
   - `memory/context/people.md` — contexto de pessoas
   - `memory/projects/<projeto>.md` — se projeto foi discutido
   - Verificar se há outros arquivos que referenciam as entidades atualizadas

3. **Criar log da sessão** em `memory/sessions/YYYY-MM-DD.md`:
   ```markdown
   # Session — YYYY-MM-DD HH:MM
   
   ## Contexto
   <resumo de 1-2 frases do que foi feito>
   
   ## Decisões
   - ...
   
   ## Pendências abertas
   - ...
   
   ## Pendências fechadas
   - ...
   ```

4. **Salvar** — simplesmente edite os arquivos acima. O file watcher do Brain Repo detectará as mudanças e fará commit+push automaticamente em até 30 segundos. Não execute `git` manualmente.

5. **Confirmar** — diga ao usuário quais arquivos foram atualizados e que o Brain Repo sincronizará em breve.

**Nota:** Se o Brain Repo não estiver configurado, o passo 4 apenas informa que as mudanças ficam salvas localmente.
