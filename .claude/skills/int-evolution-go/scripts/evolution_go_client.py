#!/usr/bin/env python3
"""
Evolution Go client - calls the Evolution Go REST API directly.
No third-party SDK dependency.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def _load_dotenv():
    """Load .env from workspace root."""
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


def get_config():
    url = os.environ.get("EVOLUTION_GO_URL")
    key = os.environ.get("EVOLUTION_GO_KEY")
    if not url:
        print(json.dumps({"error": "EVOLUTION_GO_URL env var not set"}))
        sys.exit(1)
    if not key:
        print(json.dumps({"error": "EVOLUTION_GO_KEY env var not set"}))
        sys.exit(1)
    return url.rstrip("/"), key


def _retry_http_call_client(do_call, max_attempts=3, base_delay=2.0, max_delay=8.0):
    """Exponential backoff + jitter for Evolution Go API calls.

    Retries on HTTP 5xx, urllib.error.URLError, and socket.timeout (transient).
    NEVER retries on HTTP 4xx (deterministic client errors).

    Returns the result of do_call() on success.
    Raises the last exception after max_attempts are exhausted.
    Raises immediately on HTTP 4xx (no retry).
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return do_call()
        except urllib.error.HTTPError as e:
            if e.code < 500:
                # 4xx — deterministic, raise immediately (caller decides sys.exit vs raise)
                raise
            last_exc = e
            if attempt < max_attempts - 1:
                delay = min(base_delay ** attempt + random.uniform(0, 0.5), max_delay)
                print(
                    json.dumps({
                        "evt": "api_request_retry",
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "http_status": e.code,
                        "delay_s": round(delay, 2),
                    })
                )
                time.sleep(delay)
            else:
                print(
                    json.dumps({
                        "evt": "api_request_failed",
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "http_status": e.code,
                        "category": "transient",
                    })
                )
        except (urllib.error.URLError, socket.timeout) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = min(base_delay ** attempt + random.uniform(0, 0.5), max_delay)
                print(
                    json.dumps({
                        "evt": "api_request_retry",
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "error": str(e),
                        "delay_s": round(delay, 2),
                    })
                )
                time.sleep(delay)
            else:
                print(
                    json.dumps({
                        "evt": "api_request_failed",
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "error": str(e),
                        "category": "transient",
                    })
                )
    raise last_exc
def api_request(method, path, data=None):
    """Make an HTTP request to the Evolution Go API."""
    base_url, api_key = get_config()
    url = f"{base_url}{path}"

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "apikey": api_key,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            if raw:
                return json.loads(raw)
            return {"message": "success"}
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read())
        except Exception:
            error_body = {"error": str(e)}
        print(json.dumps({"error": f"HTTP {e.code}", "details": error_body}, indent=2))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"error": f"Connection failed: {e.reason}"}))
        sys.exit(1)


def to_jid(number):
    """Convert a plain phone number to WhatsApp JID if needed."""
    if not number:
        return number
    if "@" in number:
        return number
    return f"{number}@s.whatsapp.net"


def parse_quoted(quoted_str):
    """Parse --quoted JSON string."""
    if not quoted_str:
        return None
    try:
        return json.loads(quoted_str)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON for --quoted parameter"}))
        sys.exit(1)


def add_send_opts(payload, args):
    """Add common send options (delay, quoted, mentions) to payload."""
    if hasattr(args, "delay") and args.delay:
        payload["delay"] = int(args.delay)
    if hasattr(args, "quoted") and args.quoted:
        payload["quoted"] = parse_quoted(args.quoted)
    if hasattr(args, "mentionAll") and args.mentionAll:
        payload["mentionAll"] = True
    if hasattr(args, "mentionedJid") and args.mentionedJid:
        payload["mentionedJid"] = [j.strip() for j in args.mentionedJid.split(",")]
    return payload


# ── Instance Management ──────────────────────────────────────────────

def cmd_instances(args):
    result = api_request("GET", "/instance/all")
    print(json.dumps(result, indent=2))


def cmd_create_instance(args):
    data = {"instanceName": args.name}
    if args.token:
        data["token"] = args.token
    result = api_request("POST", "/instance/create", data=data)
    print(json.dumps(result, indent=2))


def cmd_connect(args):
    data = {
        "phone": args.phone,
        "webhook": args.webhook,
    }
    if args.immediate:
        data["immediate"] = True
    if args.subscribe:
        data["subscribe"] = [s.strip() for s in args.subscribe.split(",")]
    result = api_request("POST", "/instance/connect", data=data)
    print(json.dumps(result, indent=2))


def cmd_disconnect(args):
    result = api_request("POST", "/instance/disconnect")
    print(json.dumps(result, indent=2))


def cmd_status(args):
    result = api_request("GET", "/instance/status")
    print(json.dumps(result, indent=2))


def cmd_qr(args):
    result = api_request("GET", "/instance/qr")
    print(json.dumps(result, indent=2))


def cmd_pair(args):
    data = {"phone": args.phone}
    result = api_request("POST", "/instance/pair", data=data)
    print(json.dumps(result, indent=2))


def cmd_logout(args):
    result = api_request("DELETE", "/instance/logout")
    print(json.dumps(result, indent=2))


def cmd_delete_instance(args):
    result = api_request("DELETE", f"/instance/delete/{args.instanceId}")
    print(json.dumps(result, indent=2))


def cmd_delete_proxy(args):
    result = api_request("DELETE", f"/instance/proxy/{args.instanceId}")
    print(json.dumps(result, indent=2))


def cmd_summary(args):
    """Overview: list all instances and fetch status for each."""
    instances = api_request("GET", "/instance/all")
    # If the response is a list, enrich with status info
    if isinstance(instances, list):
        summary = []
        for inst in instances:
            entry = {
                "instance": inst.get("instanceName") or inst.get("name") or inst.get("id", "unknown"),
                "raw": inst,
            }
            summary.append(entry)
        print(json.dumps({"instances": summary, "total": len(summary)}, indent=2))
    else:
        # Might already be an object with data
        print(json.dumps(instances, indent=2))


# ── Proxy Management ────────────────────────────────────────────────

def cmd_set_proxy(args):
    """Set proxy on an instance."""
    proxy = {
        "host": args.host,
        "port": int(args.port),
        "protocol": args.protocol,
        "username": args.username,
        "password": args.password,
    }
    result = api_request("POST", f"/instance/proxy/{args.instanceId}", data=proxy)
    print(json.dumps(result, indent=2))


def cmd_get_proxy(args):
    """Get proxy configuration of an instance."""
    result = api_request("GET", f"/instance/proxy/{args.instanceId}")
    print(json.dumps(result, indent=2))


def cmd_delete_proxy(args):
    result = api_request("DELETE", f"/instance/proxy/{args.instanceId}")
    print(json.dumps(result, indent=2))
# ── Send Messages ────────────────────────────────────────────────────

def cmd_send_text(args):
    payload = {
        "number": to_jid(args.number),
        "text": args.text,
    }
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/text", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_media(args):
    payload = {
        "number": to_jid(args.number),
        "mediaUrl": args.url,
        "mediatype": args.type,
    }
    if args.caption:
        payload["caption"] = args.caption
    if args.filename:
        payload["fileName"] = args.filename
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/media", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_location(args):
    payload = {
        "number": to_jid(args.number),
        "latitude": float(args.lat),
        "longitude": float(args.lng),
    }
    if args.name:
        payload["name"] = args.name
    if args.address:
        payload["address"] = args.address
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/location", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_contact(args):
    payload = {
        "number": to_jid(args.number),
        "contact": {
            "fullName": args.fullName,
            "phoneNumber": args.phone,
        },
    }
    if args.org:
        payload["contact"]["organization"] = args.org
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/contact", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_link(args):
    payload = {
        "number": to_jid(args.number),
        "url": args.url,
        "text": args.text,
    }
    if args.title:
        payload["title"] = args.title
    if args.description:
        payload["description"] = args.description
    if args.imgUrl:
        payload["thumbnailUrl"] = args.imgUrl
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/link", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_sticker(args):
    payload = {
        "number": to_jid(args.number),
        "stickerUrl": args.sticker,
    }
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/sticker", data=payload)
    print(json.dumps(result, indent=2))


def cmd_send_poll(args):
    payload = {
        "number": to_jid(args.number),
        "question": args.question,
        "options": [o.strip() for o in args.options.split(",")],
    }
    if args.maxAnswer:
        payload["maxAnswer"] = int(args.maxAnswer)
    payload = add_send_opts(payload, args)
    result = api_request("POST", "/send/poll", data=payload)
    print(json.dumps(result, indent=2))


# ── Message Operations ───────────────────────────────────────────────

def cmd_react(args):
    payload = {
        "number": to_jid(args.number),
        "messageId": args.id,
        "reaction": args.reaction,
    }
    result = api_request("POST", "/message/react", data=payload)
    print(json.dumps(result, indent=2))


def cmd_edit_message(args):
    payload = {
        "chat": args.chat,
        "messageId": args.id,
        "message": args.message,
    }
    result = api_request("POST", "/message/edit", data=payload)
    print(json.dumps(result, indent=2))


def cmd_delete_message(args):
    payload = {
        "chat": args.chat,
        "messageId": args.id,
    }
    result = api_request("POST", "/message/delete", data=payload)
    print(json.dumps(result, indent=2))


def cmd_mark_read(args):
    payload = {
        "number": to_jid(args.number),
        "messageIds": [mid.strip() for mid in args.ids.split(",")],
    }
    result = api_request("POST", "/message/markread", data=payload)
    print(json.dumps(result, indent=2))


def cmd_message_status(args):
    payload = {
        "messageId": args.id,
    }
    result = api_request("POST", "/message/status", data=payload)
    print(json.dumps(result, indent=2))


def cmd_set_presence(args):
    payload = {
        "number": to_jid(args.number),
        "state": args.state,
    }
    if args.isAudio:
        payload["isAudio"] = True
    result = api_request("POST", "/message/presence", data=payload)
    print(json.dumps(result, indent=2))


# ── CLI Parser ───────────────────────────────────────────────────────

def add_common_send_args(parser):
    """Add shared send options to a subparser."""
    parser.add_argument("--delay", help="Delay in ms before sending")
    parser.add_argument("--quoted", help="Quoted message JSON (messageId, participant)")
    parser.add_argument("--mentionAll", action="store_true", help="Mention all participants")
    parser.add_argument("--mentionedJid", help="Comma-separated JIDs to mention")
    parser.add_argument("--json", action="store_true", help="Raw JSON output")


def main():
    parser = argparse.ArgumentParser(description="Evolution Go REST API client")
    sub = parser.add_subparsers(dest="command")

    # ── Instance Management ──
    p = sub.add_parser("instances", help="List all instances")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("create_instance", help="Create a new instance")
    p.add_argument("name", help="Instance name")
    p.add_argument("--token", help="Optional auth token")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("connect", help="Connect to instance")
    p.add_argument("--phone", required=True, help="Phone number")
    p.add_argument("--webhook", required=True, help="Webhook URL")
    p.add_argument("--immediate", action="store_true", help="Connect immediately")
    p.add_argument("--subscribe", help="Comma-separated webhook events")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("disconnect", help="Disconnect from instance")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("status", help="Get instance status")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("qr", help="Get QR code for pairing")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pair", help="Request pairing code")
    p.add_argument("--phone", required=True, help="Phone number")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("logout", help="Logout from instance")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("delete_instance", help="Delete an instance")
    p.add_argument("instanceId", help="Instance ID to delete")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("summary", help="Overview of all instances with status")
    p.add_argument("--json", action="store_true")

    # ── Proxy Management ──
    p = sub.add_parser("set_proxy", help="Set proxy on an instance")
    p.add_argument("instanceId", help="Instance ID")
    p.add_argument("--host", required=True, help="Proxy host")
    p.add_argument("--port", required=True, help="Proxy port")
    p.add_argument("--protocol", default="http", help="Proxy protocol")
    p.add_argument("--username", required=True, help="Proxy username")
    p.add_argument("--password", required=True, help="Proxy password")

    p = sub.add_parser("get_proxy", help="Get proxy configuration of an instance")
    p.add_argument("instanceId", help="Instance ID")

    p = sub.add_parser("delete_proxy", help="Remove proxy from an instance")
    p.add_argument("instanceId", help="Instance ID")

    # ── Send Messages ──
    p = sub.add_parser("send_text", help="Send text message")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("text", help="Message text")
    add_common_send_args(p)

    p = sub.add_parser("send_media", help="Send media message")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--url", required=True, help="Media URL")
    p.add_argument("--type", required=True, choices=["image", "video", "audio", "document"], help="Media type")
    p.add_argument("--caption", help="Media caption")
    p.add_argument("--filename", help="Filename for documents")
    add_common_send_args(p)

    p = sub.add_parser("send_location", help="Send location")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--lat", required=True, help="Latitude")
    p.add_argument("--lng", required=True, help="Longitude")
    p.add_argument("--name", help="Location name")
    p.add_argument("--address", help="Location address")
    add_common_send_args(p)

    p = sub.add_parser("send_contact", help="Send contact vCard")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--fullName", required=True, help="Contact full name")
    p.add_argument("--phone", required=True, help="Contact phone number")
    p.add_argument("--org", help="Contact organization")
    add_common_send_args(p)

    p = sub.add_parser("send_link", help="Send link preview")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--url", required=True, help="Link URL")
    p.add_argument("--text", required=True, help="Message text")
    p.add_argument("--title", help="Link title")
    p.add_argument("--description", help="Link description")
    p.add_argument("--imgUrl", help="Thumbnail image URL")
    add_common_send_args(p)

    p = sub.add_parser("send_sticker", help="Send sticker")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--sticker", required=True, help="Sticker URL")
    add_common_send_args(p)

    p = sub.add_parser("send_poll", help="Send poll")
    p.add_argument("number", help="Recipient phone number")
    p.add_argument("--question", required=True, help="Poll question")
    p.add_argument("--options", required=True, help="Comma-separated poll options")
    p.add_argument("--maxAnswer", help="Max selectable answers")
    add_common_send_args(p)

    # ── Message Operations ──
    p = sub.add_parser("react", help="React to a message")
    p.add_argument("number", help="Chat phone number")
    p.add_argument("--id", required=True, help="Message ID")
    p.add_argument("--reaction", required=True, help="Emoji reaction")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("edit_message", help="Edit a sent message")
    p.add_argument("--chat", required=True, help="Chat JID")
    p.add_argument("--id", required=True, help="Message ID")
    p.add_argument("--message", required=True, help="New message text")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("delete_message", help="Delete message for everyone")
    p.add_argument("--chat", required=True, help="Chat JID")
    p.add_argument("--id", required=True, help="Message ID")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mark_read", help="Mark messages as read")
    p.add_argument("number", help="Chat phone number")
    p.add_argument("--ids", required=True, help="Comma-separated message IDs")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("message_status", help="Get message delivery status")
    p.add_argument("--id", required=True, help="Message ID")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("set_presence", help="Set chat presence")
    p.add_argument("number", help="Chat phone number")
    p.add_argument("--state", required=True, choices=["composing", "recording"], help="Presence state")
    p.add_argument("--isAudio", action="store_true", help="Set audio recording presence")
    p.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "instances": cmd_instances,
        "create_instance": cmd_create_instance,
        "connect": cmd_connect,
        "disconnect": cmd_disconnect,
        "status": cmd_status,
        "qr": cmd_qr,
        "pair": cmd_pair,
        "logout": cmd_logout,
        "delete_instance": cmd_delete_instance,
        "delete_proxy": cmd_delete_proxy,
        "summary": cmd_summary,
        "send_text": cmd_send_text,
        "send_media": cmd_send_media,
        "send_location": cmd_send_location,
        "send_contact": cmd_send_contact,
        "send_link": cmd_send_link,
        "send_sticker": cmd_send_sticker,
        "send_poll": cmd_send_poll,
        "react": cmd_react,
        "edit_message": cmd_edit_message,
        "delete_message": cmd_delete_message,
        "mark_read": cmd_mark_read,
        "message_status": cmd_message_status,
        "set_presence": cmd_set_presence,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        print(json.dumps({"error": f"Unknown command: {args.command}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
