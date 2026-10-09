"""RAG: chunking, BM25 retrieval, LLM answers with offline fallback."""
from __future__ import annotations

import math
import re
import threading

TOKEN = re.compile(r"[a-z0-9]+")
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "for",
    "with", "as", "at", "by", "this", "that", "you", "your", "we", "our",
    "are", "be", "was", "were", "has", "have", "had", "what", "when", "where",
    "which", "who", "how", "can", "could", "would", "should", "do", "does",
    "did", "will", "from", "about", "into", "over", "after", "all", "any",
    "not", "but", "if", "they", "them", "their", "there", "then", "than",
    "its", "also", "per", "such", "please", "me", "my", "i",
}

_lock = threading.Lock()
_index_cache: dict[int, dict] = {}


def _stem(word: str) -> str:
    """Tiny suffix stemmer so 'prices' matches 'pricing', 'hours' matches 'hour'."""
    w = word.lower()
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith("es") and not w.endswith(("ses", "xes", "ches", "shes")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 4 and w.endswith("e") and w[-2] not in "aeiou":
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    toks = TOKEN.findall(text.lower())
    return [_stem(t) for t in toks if t not in STOP and len(t) > 1]


def chunk_text(text: str, size: int = 1100, overlap: int = 180) -> list[str]:
    """Paragraph-aware sliding-window chunking (by characters)."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paras:
        if len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            sents = re.split(r"(?<=[.!?])\s+", para)
            buf = ""
            for s in sents:
                if len(buf) + len(s) + 1 > size and buf:
                    chunks.append(buf)
                    buf = s
                else:
                    buf = (buf + " " + s).strip()
            if buf:
                chunks.append(buf)
            continue
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current)
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if current:
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]


def build_index(docs: list[dict]) -> dict:
    df: dict[str, int] = {}
    lengths, tokenized = [], []
    for d in docs:
        toks = tokenize(d["text"])
        tokenized.append(toks)
        lengths.append(len(toks) or 1)
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    return {"docs": docs, "tokenized": tokenized, "lengths": lengths, "df": df}


def bm25_search(index: dict, query: str, k: int = 4, min_score: float = 0.0) -> list[dict]:
    q = tokenize(query)
    if not q or not index["docs"]:
        return []
    n = len(index["docs"])
    avgdl = sum(index["lengths"]) / max(n, 1)
    k1, b = 1.5, 0.75
    scores = [0.0] * n
    for term in q:
        df = index["df"].get(term, 0)
        if not df:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for i, toks in enumerate(index["tokenized"]):
            tf = toks.count(term)
            if not tf:
                continue
            denom = tf + k1 * (1 - b + b * index["lengths"][i] / avgdl)
            scores[i] += idf * (tf * (k1 + 1) / denom)
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
    out = []
    for i in ranked[: max(1, k)]:
        if scores[i] <= min_score:
            continue
        out.append({"score": round(scores[i], 3), **index["docs"][i]})
    return out


def load_bot_index(app, bot_id: int, force: bool = False) -> dict:
    """Build (and cache) a BM25 index over all ready chunks of one bot."""
    from . import store

    with _lock:
        entry = _index_cache.get(bot_id)
        stamp = store.bot_chunks_stamp(app, bot_id)
        if not force and entry and entry["stamp"] == stamp:
            return entry["index"]
        docs = store.bot_chunks(app, bot_id)
        index = build_index(docs)
        _index_cache[bot_id] = {"stamp": stamp, "index": index}
        return index


def invalidate_bot_index(bot_id: int) -> None:
    with _lock:
        _index_cache.pop(bot_id, None)


def resolve_provider(app, user: dict) -> tuple[str, str, str]:
    """Return (provider, api_key, model). provider in {openai, anthropic, offline}."""
    import os

    provider = (user.get("ai_provider") or "auto").lower()
    key = (user.get("ai_api_key") or "").strip()
    model = (user.get("ai_model") or "").strip()
    if provider == "auto":
        if key:
            provider = detect_provider(key)
        elif os.environ.get("OPENAI_API_KEY"):
            provider, key = "openai", os.environ["OPENAI_API_KEY"]
            model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider, key = "anthropic", os.environ["ANTHROPIC_API_KEY"]
            model = model or os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        else:
            provider = "offline"
    if provider in ("openai", "anthropic") and not key:
        return "offline", "", model
    if provider == "openai" and not model:
        model = "gpt-4o-mini"
    if provider == "anthropic" and not model:
        model = "claude-3-5-haiku-latest"
    return provider, key, model


def detect_provider(key: str) -> str:
    if key.startswith("sk-ant-"):
        return "anthropic"
    return "openai"


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    import json
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def llm_answer(provider: str, key: str, model: str, system: str, history: list[dict],
               context: str, temperature: float) -> str:
    user_text = history[-1]["content"] if history else ""
    prior = "\n".join(f'{"Customer" if m["role"] == "user" else "Assistant"}: {m["content"]}'
                      for m in history[:-1][-6:])
    if provider == "openai":
        msgs = [{"role": "system",
                 "content": system + ("\n\nKNOWLEDGE BASE:\n" + context if context
                                      else "\n\nNo knowledge base content matched. Say so honestly.")}]
        if prior:
            msgs.append({"role": "user", "content": "Earlier in this conversation:\n" + prior})
            msgs.append({"role": "assistant", "content": "Understood, continuing."})
        msgs.append({"role": "user", "content": user_text})
        out = _post_json("https://api.openai.com/v1/chat/completions",
                         {"model": model, "messages": msgs, "temperature": temperature, "max_tokens": 700},
                         {"Authorization": f"Bearer {key}"})
        return out["choices"][0]["message"]["content"].strip()
    if provider == "anthropic":
        blocks = []
        if prior:
            blocks.append({"role": "user", "content": "Earlier in this conversation:\n" + prior})
            blocks.append({"role": "assistant", "content": "Understood, continuing."})
        blocks.append({"role": "user", "content": user_text})
        out = _post_json("https://api.anthropic.com/v1/messages",
                         {"model": model, "max_tokens": 700, "temperature": temperature,
                          "system": system + ("\n\nKNOWLEDGE BASE:\n" + context if context
                                              else "\n\nNo knowledge base content matched. Say so honestly."),
                          "messages": blocks},
                         {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in out.get("content", [])
                       if b.get("type") == "text").strip()
    raise ValueError("Unknown provider")


SYNONYM_HINTS = {
    "pric": ["pricing", "cost", "plan", "subscription"],
    "cost": ["pricing", "price", "plan"],
    "refund": ["money back", "return", "cancel"],
    "hour": ["support hours", "available", "open"],
    "support": ["help", "contact", "hours"],
    "security": ["sso", "soc", "encryption"],
    "secur": ["sso", "soc", "encryption"],
    "start": ["getting started", "setup", "onboard"],
}


def _query_terms(query: str) -> set[str]:
    """Query tokens expanded with synonym stems."""
    base = set(tokenize(query))
    expanded = set(base)
    for t in base:
        for syn in SYNONYM_HINTS.get(t, []):
            expanded.update(tokenize(syn))
    return expanded


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown text into (header, body) sections."""
    sections: list[tuple[str, str]] = []
    header, buf = "", []
    for line in (text or "").splitlines():
        m = re.match(r"^#{1,4}\s+(.*)", line.strip())
        if m:
            if buf or header:
                sections.append((header, "\n".join(buf).strip()))
            header, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if buf or header:
        sections.append((header, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b]


def _pick_sentences(text: str, query: str, limit: int = 3, max_chars: int = 800) -> str:
    """Section-aware extractive snippet: prefer the section whose header
    matches the query, else the sentences with the most query overlap."""
    qt = _query_terms(query)
    sections = _split_sections(text)
    if sections:
        best_h, best_score = "", -1
        for h, b in sections:
            ht = set(tokenize(h))
            score = len(qt & ht)
            if score > best_score:
                best_h, best_score = h, score
        if best_score > 0:
            for h, b in sections:
                if h == best_h:
                    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", b) if s.strip()]
                    return " ".join(sents[:4])[:max_chars]
    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
    sents = [s for s in raw if not re.match(r"^#{1,4}\s", s)] or raw
    scored = []
    for s in sents:
        st = set(tokenize(s))
        scored.append((len(qt & st), s))
    scored.sort(key=lambda t: -t[0])
    order = {s: i for i, s in enumerate(sents)}
    picked = [s for _, s in scored[:limit]]
    picked.sort(key=lambda s: order.get(s, 0))
    return " ".join(picked)[:max_chars]

def offline_answer(query: str, hits: list[dict], bot_name: str) -> str:
    """Honest extractive answer from retrieved chunks (no API key configured)."""
    if not hits:
        return (f"I couldn't find anything about that in {bot_name}'s knowledge base yet. "
                "Try asking about the topics in the indexed sources, or add more content in Data sources.")
    best = hits[0]
    snippet = _pick_sentences(best["text"], query)
    others = [f"- {h['label']}" for h in hits[1:3]]
    tail = ("\n\nRelated sources:\n" + "\n".join(others)) if others else ""
    note = "_Answered from the knowledge base (offline retrieval mode)._"
    return f"Based on **{best['label']}**:\n\n{snippet}{tail}\n\n{note}"
