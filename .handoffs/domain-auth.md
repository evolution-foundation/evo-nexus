# Domain Handoff: GitHub Auth Architecture

## Interface abstrata (criada na Fase 2)

```python
# dashboard/backend/brain_repo/github_oauth.py
class GitHubAuthProvider:
    def get_token(self) -> str: ...         # plaintext para requests
    def encrypt_token(self) -> bytes: ...   # Fernet-encrypted para banco
```

## Implementação atual: PAT

```python
class PATAuthProvider(GitHubAuthProvider):
    def __init__(self, pat: str, master_key: bytes): ...
```

## Implementação futura: OAuth (atrás de feature flag)

```python
class OAuthAuthProvider(GitHubAuthProvider):
    # Só instanciada se EVO_NEXUS_GITHUB_CLIENT_ID está no env
    def start_oauth_flow(client_id, redirect_uri) -> str: ...
    def handle_callback(code, client_id, client_secret) -> bytes: ...
```

**Regra:** trocar PAT por OAuth = trocar qual classe é instanciada. Zero refactor nas camadas acima.

## Onde o token fica

- **Banco (SQLite):** `brain_repo_configs.github_token_encrypted` (BLOB)
- **Chave de criptografia:** `BRAIN_REPO_MASTER_KEY` no `.env` (gerado no setup)
- **Plaintext em memória:** nunca persistido, só usado durante operações de git/API

## Funções utilitárias

```python
decrypt_token(encrypted: bytes, master_key: bytes) -> str
is_oauth_enabled() -> bool  # bool(os.getenv("EVO_NEXUS_GITHUB_CLIENT_ID"))
```

## Status

- [ ] Fase 2: Criar `github_oauth.py` com `PATAuthProvider` + `OAuthAuthProvider` (esqueleto)
- [ ] Fase 2: Criar `github_api.py` com todas as funções
- [ ] Fase 6: Usar nos endpoints de backend
- [ ] Fase 7: Expor na UI (StepBrainConnect + Integrations card)
