"""Shared chat answering: retrieve -> LLM/offline -> persist."""
from __future__ import annotations

import json

from . import rag, store


DEFAULT_GUARDRAILS = {
    "confidence_threshold": 0,      # min BM25 score to answer from KB (0 = off)
    "fallback_message": "",         # shown when confidence too low
    "escalate_keywords": [],        # keywords that trigger human handoff
    "escalation_message": "",       # message shown when escalating
}


def get_guardrails(bot) -> dict:
    """Merge stored guardrails over defaults. `bot['guardrails']` is a dict (or {})."""
    g = dict(DEFAULT_GUARDRAILS)
    stored = bot.get("guardrails") or {}
    if isinstance(stored, str):
        import json
        try:
            stored = json.loads(stored or "{}")
        except (ValueError, TypeError):
            stored = {}
    for k in DEFAULT_GUARDRAILS:
        if k in stored and stored[k] not in (None, ""):
            g[k] = stored[k]
    # coerce types
    try:
        g["confidence_threshold"] = float(g["confidence_threshold"] or 0)
    except (ValueError, TypeError):
        g["confidence_threshold"] = 0.0
    if isinstance(g["escalate_keywords"], str):
        g["escalate_keywords"] = [k.strip() for k in g["escalate_keywords"].split(",") if k.strip()]
    return g


def evaluate_guardrails(bot, query: str, hits: list[dict]) -> dict | None:
    """Return a guardrail result dict if a guardrail fires, else None.

    Result: {kind: 'escalate'|'fallback', text: str, reason: str, score: float}
    """
    g = get_guardrails(bot)
    low = query.lower()

    # 1) keyword escalation takes priority (e.g. refund, cancel, lawyer)
    for kw in g["escalate_keywords"]:
        if kw and kw.lower() in low:
            return {
                "kind": "escalate",
                "reason": f"keyword:{kw}",
                "score": 0.0,
                "text": g["escalation_message"]
                or "I've flagged this for a human agent who will follow up shortly.",
            }

    # 2) low-confidence fallback (only when there is KB content to compare against)
    threshold = g["confidence_threshold"]
    if threshold > 0:
        best = max((h.get("score", 0) for h in hits), default=0.0)
        if not hits or best < threshold:
            return {
                "kind": "fallback",
                "reason": "low_confidence",
                "score": best,
                "text": g["fallback_message"]
                or "I'm not fully sure about that. Let me connect you with a teammate who can help.",
            }
    return None


def bot_system_prompt(bot, user) -> str:
    custom = (bot.get("system_prompt") or "").strip()
    if custom:
        return custom
    company = (user.get("workspace") or "the company").replace("'s workspace", "")
    return store.DEFAULT_SYSTEM.format(name=bot["name"], company=company)


def answer_turn(app, bot, user, history: list[dict], query: str, conv_id: int | None = None) -> dict:
    """history: prior [{role, content}] (without the new query). Returns reply dict."""
    # ---- Voiceflow-like flow engine: route through flow if bot has one ----
    flow_ref = bot.get("flow") or ""
    flow_id = None
    try:
        flow_ref_parsed = json.loads(flow_ref) if isinstance(flow_ref, str) else flow_ref
        if isinstance(flow_ref_parsed, dict):
            flow_id = flow_ref_parsed.get("flow_id")
        elif isinstance(flow_ref_parsed, str) and flow_ref_parsed:
            flow_id = flow_ref_parsed
    except (ValueError, TypeError):
        flow_id = flow_ref if flow_ref else None

    if flow_id:
        from .flow_engine import FlowEngine
        flow_engine = FlowEngine(app)
        session = flow_engine.get_session_by_conversation(conv_id) if conv_id else None
        if not session:
            session = flow_engine.start_session(flow_id, user["id"], bot["id"], conv_id)
        result = flow_engine.advance(session["id"], query)
        if result.get("response"):
            store.add_message(app, conv_id, "assistant", result["response"],
                              citations=result.get("citations"))
            if not store.get_conversation(app, conv_id).get("title"):
                store.set_conversation(app, conv_id, user["id"], title=query[:60])
            return {"text": result["response"], "citations": result.get("citations") or [],
                    "provider": result.get("provider", "flow"),
                    "model": result.get("model", "flow"),
                    "guardrail": None, "guardrail_reason": None}
        # Flow is waiting for input or produced no response; fall through to RAG

    index = rag.load_bot_index(app, bot["id"])
    hits = rag.bm25_search(index, query, k=4)
    provider, key, model = rag.resolve_provider(app, user)

    # ---- guardrails: escalation / low-confidence fallback ----
    gr = evaluate_guardrails(bot, query, hits)
    if gr:
        if gr["kind"] == "escalate" and conv_id:
            try:
                store.set_conversation(app, conv_id, user["id"], status="needs_human")
                handle_refund_request(app, conv_id, bot, user)  # fire handoff webhook
            except Exception:
                pass
        return {"text": gr["text"], "citations": [], "provider": provider, "model": model,
                "guardrail": gr["kind"], "guardrail_reason": gr["reason"]}

    citations = [{"source": h["label"], "score": h["score"]} for h in hits[:3]]
    if provider == "offline":
        text = rag.offline_answer(query, hits, bot["name"])
    else:
        context = "\n\n---\n\n".join(
            f"Source: {h['label']}\n{h['text'][:1500]}" for h in hits[:4])
        try:
            text = rag.llm_answer(provider, key, model, bot_system_prompt(bot, user),
                                  history + [{"role": "user", "content": query}],
                                  context, float(bot.get("temperature") or 0.4))
        except Exception as exc:
            text = (rag.offline_answer(query, hits, bot["name"])
                    + f"\n\n_(Live model unavailable: {exc}. Add a valid API key in Settings.)_")
            provider = "offline"
    return {"text": text, "citations": citations, "provider": provider, "model": model}


def handle_refund_request(app, conv_id, bot, user):
    fire = getattr(store, "fire_webhooks", None)
    if fire:
        try:
            fire(app, user["id"], "handoff.requested",
                 {"bot": bot["name"], "conversation_id": conv_id, "reason": "refund"})
        except Exception:
            pass


def maybe_capture_lead(app, bot, user, conv_id, email, name=""):
    if email and "@" in email:
        store.upsert_contact(app, bot["id"], user["id"], name=name, email=email)
        store.set_conversation(app, conv_id, user["id"], email=email,
                               **({"name": name} if name else {}))
        return True
    return False
