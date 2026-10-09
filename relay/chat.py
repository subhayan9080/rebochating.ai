"""Shared chat answering: retrieve -> LLM/offline -> persist."""
from __future__ import annotations

from . import rag, store


def bot_system_prompt(bot, user) -> str:
    custom = (bot.get("system_prompt") or "").strip()
    if custom:
        return custom
    company = (user.get("workspace") or "the company").replace("'s workspace", "")
    return store.DEFAULT_SYSTEM.format(name=bot["name"], company=company)


def answer_turn(app, bot, user, history: list[dict], query: str) -> dict:
    """history: prior [{role, content}] (without the new query). Returns reply dict."""
    index = rag.load_bot_index(app, bot["id"])
    hits = rag.bm25_search(index, query, k=4)
    provider, key, model = rag.resolve_provider(app, user)
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
