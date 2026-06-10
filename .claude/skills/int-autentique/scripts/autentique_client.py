#!/usr/bin/env python3
"""
Autentique GraphQL API v2 client — assinatura eletrônica brasileira.

Endpoint: https://api.autentique.com.br/v2/graphql
Auth: header Authorization Bearer (env AUTENTIQUE_API_TOKEN).

Operations: documents create/get/list/download/delete + folders list + whoami.

Multipart upload (createDocument) segue spec graphql-multipart-request.

No third-party SDK. Stdlib only.
"""
import argparse
import io
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


GRAPHQL_URL = "https://api.autentique.com.br/v2/graphql"
DEFAULT_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = [1, 2, 4]


def _load_dotenv():
    """Carrega .env do raiz do workspace (4 níveis acima)."""
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _token():
    t = os.environ.get("AUTENTIQUE_API_TOKEN")
    if not t:
        print(json.dumps({
            "error": "AUTENTIQUE_API_TOKEN ausente no .env",
            "details": "Gere em https://painel.autentique.com.br/perfil/api e adicione AUTENTIQUE_API_TOKEN=... no .env"
        }))
        sys.exit(1)
    return t


def output(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# GraphQL request — JSON simples (queries e mutations sem upload de arquivo)
# ---------------------------------------------------------------------------

def gql(query, variables=None):
    body = json.dumps({
        "query": query,
        "variables": variables or {},
    }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "EvoNexus/1.0 int-autentique",
    }

    last_error = None
    for attempt in range(RETRY_ATTEMPTS):
        req = urllib.request.Request(GRAPHQL_URL, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                raw = resp.read()
                payload = json.loads(raw)
                if "errors" in payload:
                    print(json.dumps({"error": "GraphQL errors", "details": payload["errors"]}, indent=2, ensure_ascii=False))
                    sys.exit(2)
                return payload.get("data", {})
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read())
            except Exception:
                err_body = {"message": str(e)}
            last_error = {"http_code": e.code, "body": err_body}
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BACKOFF[attempt])
                    continue
            print(json.dumps({"error": f"HTTP {e.code}", "details": err_body}, indent=2, ensure_ascii=False))
            sys.exit(2)
        except urllib.error.URLError as e:
            last_error = {"connection": str(e.reason)}
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF[attempt])
                continue
            print(json.dumps({"error": "Connection failed", "details": str(e.reason)}))
            sys.exit(2)

    print(json.dumps({"error": "Max retries exceeded", "details": last_error}, indent=2, ensure_ascii=False))
    sys.exit(2)


# ---------------------------------------------------------------------------
# Multipart upload (createDocument com arquivo)
# Spec: https://github.com/jaydenseric/graphql-multipart-request-spec
# ---------------------------------------------------------------------------

def gql_upload(query, variables, file_path, file_var_path="variables.file"):
    """Faz POST multipart/form-data com upload de 1 arquivo (spec GraphQL multipart)."""
    file_path = Path(file_path)
    if not file_path.exists():
        print(json.dumps({"error": "Arquivo não encontrado", "details": str(file_path)}))
        sys.exit(1)

    boundary = "----EvoNexusBoundary" + secrets.token_hex(8)
    operations = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    file_map = json.dumps({"0": [file_var_path]}).encode("utf-8")
    file_bytes = file_path.read_bytes()
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    parts = []
    parts.append(f"--{boundary}".encode())
    parts.append(b'Content-Disposition: form-data; name="operations"\r\n')
    parts.append(b"")
    parts.append(operations)
    parts.append(f"--{boundary}".encode())
    parts.append(b'Content-Disposition: form-data; name="map"\r\n')
    parts.append(b"")
    parts.append(file_map)
    parts.append(f"--{boundary}".encode())
    parts.append(f'Content-Disposition: form-data; name="0"; filename="{file_path.name}"'.encode())
    parts.append(f"Content-Type: {mime_type}".encode())
    parts.append(b"")
    parts.append(file_bytes)
    parts.append(f"--{boundary}--".encode())

    body = b"\r\n".join(parts) + b"\r\n"

    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        "User-Agent": "EvoNexus/1.0 int-autentique",
    }

    req = urllib.request.Request(GRAPHQL_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT * 2) as resp:
            raw = resp.read()
            payload = json.loads(raw)
            if "errors" in payload:
                print(json.dumps({"error": "GraphQL errors", "details": payload["errors"]}, indent=2, ensure_ascii=False))
                sys.exit(2)
            return payload.get("data", {})
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read())
        except Exception:
            err_body = {"message": str(e)}
        print(json.dumps({"error": f"HTTP {e.code}", "details": err_body}, indent=2, ensure_ascii=False))
        sys.exit(2)
    except urllib.error.URLError as e:
        print(json.dumps({"error": "Connection failed", "details": str(e.reason)}))
        sys.exit(2)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_whoami(args):
    data = gql("""
    query { me { id name email } }
    """)
    output(data)


def cmd_documents_list(args):
    data = gql("""
    query ListDocs($page: Int, $limit: Int) {
        documents(page: $page, limit: $limit) {
            total
            data {
                id
                name
                created_at
                signatures { public_id name email signed { created_at } }
            }
        }
    }
    """, {"page": args.page, "limit": args.limit})
    output(data)


def cmd_documents_get(args):
    data = gql("""
    query GetDoc($id: UUID!) {
        document(id: $id) {
            id
            name
            created_at
            sandbox
            refusable
            qualified
            files { original signed }
            signatures {
                public_id
                name
                email
                action { name }
                viewed { created_at }
                signed { created_at }
                rejected { created_at }
            }
        }
    }
    """, {"id": args.document_id})
    output(data)


def cmd_documents_create(args):
    try:
        signers = json.loads(args.signers)
        if not isinstance(signers, list) or not signers:
            raise ValueError("--signers deve ser um array JSON com pelo menos 1 signatário")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": "JSON inválido em --signers", "details": str(e)}))
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    document_input = {
        "name": args.name,
        "refusable": True,
    }
    if args.message:
        document_input["message"] = args.message
    if args.reminder:
        document_input["reminder"] = args.reminder
    if args.qualified:
        document_input["qualified"] = True
    if args.sandbox:
        document_input["sandbox"] = True

    variables = {
        "document": document_input,
        "signers": signers,
        "file": None,  # placeholder substituído pelo multipart
    }

    org_id = os.environ.get("AUTENTIQUE_ORGANIZATION_ID")
    org_arg = ", organization_id: $organization_id" if org_id else ""
    org_param = ", $organization_id: Int" if org_id else ""
    if org_id:
        variables["organization_id"] = int(org_id)

    query = f"""
    mutation CreateDoc(
        $document: DocumentInput!,
        $signers: [SignerInput!]!,
        $file: Upload!{org_param}
    ) {{
        createDocument(
            document: $document,
            signers: $signers,
            file: $file{org_arg}
        ) {{
            id
            name
            sandbox
            files {{ original }}
            signatures {{
                public_id
                name
                email
                link {{ short_link }}
            }}
        }}
    }}
    """

    data = gql_upload(query, variables, args.file)
    output(data)


def cmd_documents_download(args):
    data = gql("""
    query GetFiles($id: UUID!) {
        document(id: $id) { id name files { original signed } }
    }
    """, {"id": args.document_id})
    doc = (data or {}).get("document", {})
    files = doc.get("files", {})
    url = files.get(args.version)
    if not url:
        print(json.dumps({"error": f"URL '{args.version}' não disponível", "details": files}))
        sys.exit(2)

    out_path = Path(args.output) if args.output else Path(f"{doc.get('name', doc['id'])}.{args.version}.pdf")
    try:
        with urllib.request.urlopen(url, timeout=DEFAULT_TIMEOUT * 2) as resp:
            out_path.write_bytes(resp.read())
        output({"saved": str(out_path), "bytes": out_path.stat().st_size, "version": args.version})
    except Exception as e:
        print(json.dumps({"error": "Falha ao baixar", "details": str(e)}))
        sys.exit(2)


def cmd_documents_delete(args):
    if not args.confirm:
        print(json.dumps({
            "error": "Confirmação obrigatória",
            "details": "Adicione --confirm pra deletar (operação irreversível)"
        }))
        sys.exit(1)
    data = gql("""
    mutation DeleteDoc($id: UUID!) { deleteDocument(id: $id) }
    """, {"id": args.document_id})
    output(data)


def cmd_folders_list(args):
    data = gql("""
    query { folders(page: 1, limit: 60) { data { id name } } }
    """)
    output(data)


def cmd_smoke(args):
    import time as _time
    overall = "PASS"
    steps = []
    t_total = _time.monotonic()
    try:
        # step 1: auth — verifica token e retorna dados do usuário (reutiliza whoami)
        t0 = _time.monotonic()
        try:
            token_val = os.environ.get("AUTENTIQUE_API_TOKEN", "")
            if not token_val:
                raise ValueError("AUTENTIQUE_API_TOKEN ausente no .env")
            data = gql("query { me { id name email } }")
            me = (data or {}).get("me", {})
            steps.append({"step": "auth_whoami", "status": "PASS",
                          "email": me.get("email", ""), "duration_ms": round((_time.monotonic() - t0) * 1000)})
        except Exception as e:
            overall = "FAIL"
            steps.append({"step": "auth_whoami", "status": "FAIL",
                          "error": str(e)[:300], "duration_ms": round((_time.monotonic() - t0) * 1000)})
            print(json.dumps({"overall": overall, "steps": steps,
                              "duration_ms": round((_time.monotonic() - t_total) * 1000)}, ensure_ascii=False))
            sys.exit(0)

        # step 2: listar documentos (leitura barata, limit=1)
        t0 = _time.monotonic()
        try:
            data = gql("query { documents(page: 1, limit: 1) { total } }")
            total = (data or {}).get("documents", {}).get("total", "N/A")
            steps.append({"step": "read_documents", "status": "PASS",
                          "total_documents": total, "duration_ms": round((_time.monotonic() - t0) * 1000)})
        except Exception as e:
            overall = "FAIL"
            steps.append({"step": "read_documents", "status": "FAIL",
                          "error": str(e)[:300], "duration_ms": round((_time.monotonic() - t0) * 1000)})
    except Exception as e:
        overall = "FAIL"
        steps.append({"step": "unexpected", "status": "FAIL", "error": str(e)[:300], "duration_ms": 0})
    print(json.dumps({"overall": overall, "steps": steps,
                      "duration_ms": round((_time.monotonic() - t_total) * 1000)}, ensure_ascii=False))
    sys.exit(0)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Autentique — assinatura eletrônica BR via GraphQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s whoami
  %(prog)s documents list --limit 5
  %(prog)s documents get DOCUMENT_ID
  %(prog)s documents create \\
    --file ./contrato.pdf \\
    --name "Contrato de Prestação de Serviço" \\
    --signers '[{"email":"cliente@empresa.com","action":"SIGN"},{"email":"responsavel@empresa.com","action":"SIGN"}]' \\
    --message "Segue o contrato pra assinatura."
  %(prog)s documents download DOCUMENT_ID --version signed --output assinado.pdf
  %(prog)s documents delete DOCUMENT_ID --confirm
""",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("whoami", help="Verifica auth + retorna dados do usuário/organização")

    p_docs = sub.add_parser("documents", help="Operações com documentos")
    docs_sub = p_docs.add_subparsers(dest="subcommand")

    pc = docs_sub.add_parser("list", help="Listar documentos")
    pc.add_argument("--page", type=int, default=1)
    pc.add_argument("--limit", type=int, default=20)

    pc = docs_sub.add_parser("get", help="Obter detalhes de 1 documento")
    pc.add_argument("document_id", help="UUID do documento")

    pc = docs_sub.add_parser("create", help="Criar documento e enviar p/ assinatura")
    pc.add_argument("--file", required=True, help="Caminho do PDF/DOCX")
    pc.add_argument("--name", required=True, help="Nome do documento")
    pc.add_argument("--signers", required=True, help="Array JSON de signatários")
    pc.add_argument("--message", help="Mensagem custom para os signatários")
    pc.add_argument("--reminder", choices=["DAILY", "WEEKLY", "MONTHLY"], help="Frequência de lembrete")
    pc.add_argument("--qualified", action="store_true", help="Assinatura qualificada (ICP-Brasil)")
    pc.add_argument("--sandbox", action="store_true", help="Modo sandbox (sem custo, p/ teste)")

    pc = docs_sub.add_parser("download", help="Baixar PDF original ou assinado")
    pc.add_argument("document_id", help="UUID do documento")
    pc.add_argument("--version", choices=["original", "signed"], default="signed")
    pc.add_argument("--output", help="Caminho de saída (default: nome do doc)")

    pc = docs_sub.add_parser("delete", help="Deletar documento (irreversível)")
    pc.add_argument("document_id", help="UUID do documento")
    pc.add_argument("--confirm", action="store_true", help="Confirmação obrigatória")

    p_folders = sub.add_parser("folders", help="Operações com pastas")
    folders_sub = p_folders.add_subparsers(dest="subcommand")
    folders_sub.add_parser("list", help="Listar pastas")

    sub.add_parser("smoke", help="Smoke test: auth + leitura barata (exit 0 sempre, JSON)")

    return parser


COMMANDS = {
    ("whoami", None): cmd_whoami,
    ("documents", "list"): cmd_documents_list,
    ("documents", "get"): cmd_documents_get,
    ("documents", "create"): cmd_documents_create,
    ("documents", "download"): cmd_documents_download,
    ("documents", "delete"): cmd_documents_delete,
    ("folders", "list"): cmd_folders_list,
    ("smoke", None): cmd_smoke,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    sub = getattr(args, "subcommand", None)
    handler = COMMANDS.get((args.command, sub))
    if not handler:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": "JSON inválido em argumento", "details": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
