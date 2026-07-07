#!/usr/bin/env python3
"""
fetch_ai_trends.py — Coleta tweets sobre IA dos últimos 7 dias via X API v2,
ranqueia por viralidade (likes + RTs + quotes + bookmarks + impressões) e
agrupa por tema. Saída: JSON com os tweets top + agregação por tópico.

Uso:
    python3 fetch_ai_trends.py [--days 7] [--per-query 100] [--out trends.json]

Requer SOCIAL_TWITTER_1_BEARER_TOKEN no .env (raiz do workspace).
Tier mínimo: Basic (recent search, janela de 7 dias).
"""
import os, sys, json, time, re, argparse, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# Consultas focadas em IA (PT + EN). -is:retweet evita duplicar virais por RT.
QUERIES = [
    '("inteligência artificial" OR "IA generativa" OR "agentes de IA") lang:pt -is:retweet',
    '(ChatGPT OR Claude OR Gemini OR "GPT-5" OR Llama) lang:pt -is:retweet',
    '(AI agents OR "agentic AI" OR "AI coding" OR "AI automation") lang:en -is:retweet',
    '("AI breakthrough" OR "new AI model" OR "AI startup" OR "open source AI") lang:en -is:retweet',
    '(OpenAI OR Anthropic OR "Google DeepMind" OR xAI OR Mistral) lang:en -is:retweet',
    '("AI agents" OR chatbot OR "customer support AI" OR "WhatsApp AI") lang:en -is:retweet',
]

def score(m):
    return (
        m.get("like_count", 0) * 1.0
        + m.get("retweet_count", 0) * 2.0
        + m.get("quote_count", 0) * 1.5
        + m.get("reply_count", 0) * 0.5
        + m.get("bookmark_count", 0) * 1.5
        + m.get("impression_count", 0) * 0.001
    )

# Temas para clusterizar (label -> regex de palavras-chave)
TOPICS = {
    "Agentes de IA / Agentic": r"agent|agentic|autonom",
    "IA para código/dev": r"coding|code|developer|copilot|cursor|claude code|programaç",
    "Modelos novos (GPT/Claude/Gemini/Llama)": r"gpt-?5|gpt5|claude|gemini|llama|mistral|grok|deepseek|qwen",
    "OpenAI / Anthropic / Big Labs": r"openai|anthropic|deepmind|xai|microsoft|meta ai|google ai",
    "IA generativa de imagem/vídeo": r"image|video|midjourney|sora|veo|flux|diffusion|gerad",
    "Automação / Workflows com IA": r"automat|workflow|n8n|zapier|rpa|integraç",
    "Chatbots / Atendimento / WhatsApp": r"chatbot|whatsapp|customer support|atendimento|suporte|sac",
    "IA no trabalho / produtividade": r"productiv|produtiv|trabalho|job|emprego|workplace",
    "Open source / modelos abertos": r"open.?source|open weight|aberto",
    "Regulação / ética / segurança": r"regulat|regul|ethic|étic|safety|seguran|privac",
    "Negócios / startups / investimento": r"startup|funding|investimen|raise|bilh|billion|valuation|ipo",
}

API_ERRORS = []

def classify(text):
    t = text.lower()
    hits = [label for label, pat in TOPICS.items() if re.search(pat, t)]
    return hits or ["Outros / IA geral"]

# Ruído conhecido: idol K-pop "Gemini", aniversários, filmes, sorteios.
NOISE = re.compile(
    r"#?\d*gemini\s*day|miracle boy|ppnaravit|phuwintang|ohmpawat|aniversário|"
    r"feliz aniver|happy birthday|cinemascore|giveaway|sorteio|🎂",
    re.IGNORECASE,
)

def is_noise(text):
    return bool(NOISE.search(text or ""))

def fetch(query, bearer, start_time, max_results=100):
    params = {
        "query": query,
        "max_results": str(max_results),
        "start_time": start_time,
        "sort_order": "relevancy",
        "tweet.fields": "public_metrics,created_at,lang,author_id,entities",
        "expansions": "author_id",
        "user.fields": "username,name,public_metrics,verified",
    }
    url = "https://api.twitter.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:1000]
        API_ERRORS.append({"query": query, "status": e.code, "body": body})
        print(f"[warn] HTTP {e.code} em query '{query[:40]}...': {body}", file=sys.stderr)
        return {"data": [], "includes": {}}
    except Exception as e:
        API_ERRORS.append({"query": query, "error": str(e)})
        print(f"[warn] erro em query '{query[:40]}...': {e}", file=sys.stderr)
        return {"data": [], "includes": {}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--per-query", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "trends_raw.json"))
    args = ap.parse_args()

    env = load_env()
    bearer = env.get("SOCIAL_TWITTER_1_BEARER_TOKEN") or os.environ.get("SOCIAL_TWITTER_1_BEARER_TOKEN")
    if not bearer:
        print("ERRO: SOCIAL_TWITTER_1_BEARER_TOKEN não encontrado no .env", file=sys.stderr)
        sys.exit(1)

    start = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    users = {}
    all_tweets = {}

    for q in QUERIES:
        res = fetch(q, bearer, start, args.per_query)
        for u in res.get("includes", {}).get("users", []):
            users[u["id"]] = u
        for tw in res.get("data", []):
            if is_noise(tw.get("text", "")):
                continue
            tw["_score"] = score(tw.get("public_metrics", {}))
            tw["_topics"] = classify(tw.get("text", ""))
            all_tweets[tw["id"]] = tw
        time.sleep(1.2)  # respeita rate limit

    tweets = sorted(all_tweets.values(), key=lambda t: t["_score"], reverse=True)

    # Agregação por tema
    topic_agg = defaultdict(lambda: {"count": 0, "total_score": 0.0, "top_tweets": []})
    for tw in tweets:
        for topic in tw["_topics"]:
            agg = topic_agg[topic]
            agg["count"] += 1
            agg["total_score"] += tw["_score"]
            if len(agg["top_tweets"]) < 5:
                u = users.get(tw.get("author_id"), {})
                agg["top_tweets"].append({
                    "id": tw["id"],
                    "text": tw["text"][:280],
                    "author": u.get("username", "?"),
                    "metrics": tw.get("public_metrics", {}),
                    "score": round(tw["_score"], 1),
                    "url": f"https://x.com/i/web/status/{tw['id']}",
                })

    topics_ranked = sorted(
        ({"topic": k, **v} for k, v in topic_agg.items()),
        key=lambda x: x["total_score"], reverse=True,
    )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "queries": QUERIES,
        "api_errors": API_ERRORS,
        "total_tweets_collected": len(tweets),
        "topics_ranked": topics_ranked,
        "top_tweets_overall": [
            {
                "text": t["text"][:280],
                "author": users.get(t.get("author_id"), {}).get("username", "?"),
                "metrics": t.get("public_metrics", {}),
                "score": round(t["_score"], 1),
                "topics": t["_topics"],
                "url": f"https://x.com/i/web/status/{t['id']}",
            }
            for t in tweets[:40]
        ],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"OK: {len(tweets)} tweets coletados, {len(topics_ranked)} temas. Saída: {args.out}")

if __name__ == "__main__":
    main()
