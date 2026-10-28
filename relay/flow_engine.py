"""Flow engine: executes conversation flows node by node.

Node types: text, choice, llm, condition, api_call, variable_set, capture, handoff.
"""
from __future__ import annotations

import json
import re
import uuid

from . import rag, store
from .chat import evaluate_guardrails, handle_refund_request


def _interpolate(text, ctx):
    if not text:
        return text

    def repl(match):
        key = match.group(1).strip()
        parts = key.split(".")
        val = ctx
        for p in parts:
            if isinstance(val, dict) and p in val:
                val = val[p]
            else:
                return ""
        return str(val) if val is not None else ""

    return re.sub(r"\{\{\s*([^}]+)\s*\}\}", repl, text or "")


def _build_ctx(app, bot, user, session_vars):
    return {
        "user": {"name": user.get("name", ""), "email": user.get("email", "")},
        "bot": {"name": bot.get("name", "")},
        "session": session_vars,  # reference, not copy, so updates are visible
    }


# ------------------------------------------------------------------ node executors

def _exec_text(node, ctx):
    message = _interpolate(node.get("config", {}).get("message", ""), ctx)
    return {"response": message, "next_node": None, "variables": {}, "actions": []}


def _exec_choice(node, ctx, query=None):
    config = node.get("config", {})
    options = config.get("options", [])
    if not query:
        return {
            "response": None,
            "next_node": None,
            "variables": {},
            "actions": [{"type": "show_options", "options": options}],
        }
    q = (query or "").strip().lower()
    for opt in options:
        label = (opt.get("label") or "").strip().lower()
        if q == label or q in label or label in q:
            return {"response": None, "next_node": opt.get("next_node"), "variables": {}, "actions": []}
    return {"response": "I didn't understand that choice. Please try again.", "next_node": None, "variables": {}, "actions": []}


def _exec_llm(app, bot, user, node, ctx, query=None, history=None):
    config = node.get("config", {})
    use_knowledge = config.get("use_knowledge", True)
    prompt_template = config.get("prompt", "")
    model_override = config.get("model_override", "")

    index = rag.load_bot_index(app, bot["id"])
    hits = rag.bm25_search(index, query or "", k=4) if query else []

    gr = evaluate_guardrails(bot, query or "", hits)
    if gr:
        return {"response": gr["text"], "next_node": None, "variables": {},
                "actions": [{"type": "guardrail", "kind": gr["kind"], "reason": gr["reason"]}]}

    provider, key, model = rag.resolve_provider(app, user)
    if model_override:
        model = model_override

    if provider == "offline" or not use_knowledge or not hits:
        text = rag.offline_answer(query or "", hits, bot.get("name", ""))
    else:
        context = "\n\n---\n\n".join(
            f"Source: {h['label']}\n{h['text'][:1500]}" for h in hits[:4]
        )
        system_prompt = _interpolate(prompt_template or store.DEFAULT_SYSTEM.format(
            name=bot["name"],
            company=(user.get("workspace") or "the company").replace("'s workspace", ""),
        ), ctx)
        try:
            text = rag.llm_answer(provider, key, model, system_prompt,
                                  (history or []) + [{"role": "user", "content": query or ""}],
                                  context, float(bot.get("temperature") or 0.4))
        except Exception:
            text = rag.offline_answer(query or "", hits, bot.get("name", ""))
            provider = "offline"

    citations = [{"source": h["label"], "score": h["score"]} for h in hits[:3]]
    return {"response": text, "next_node": None, "variables": {},
            "citations": citations, "provider": provider, "model": model,
            "actions": []}


def _exec_condition(node, ctx, session_vars):
    config = node.get("config", {})
    rules = config.get("rules", [])
    for rule in rules:
        variable = rule.get("variable", "")
        operator = rule.get("operator", "equals")
        value = rule.get("value", "")
        next_node = rule.get("next_node")
        var_value = ""
        if variable.startswith("session."):
            var_value = session_vars.get(variable[8:], "")
        elif variable.startswith("user."):
            var_value = ctx.get("user", {}).get(variable[5:], "")
        match = False
        if operator == "equals":
            match = str(var_value) == str(value)
        elif operator == "contains":
            match = str(value).lower() in str(var_value).lower()
        elif operator == "not_equals":
            match = str(var_value) != str(value)
        if match:
            return {"response": None, "next_node": next_node, "variables": {}, "actions": []}
    return {"response": None, "next_node": None, "variables": {}, "actions": []}


def _exec_api_call(node, ctx):
    import requests
    config = node.get("config", {})
    url = _interpolate(config.get("url", ""), ctx)
    method = config.get("method", "GET").upper()
    response_var = config.get("response_var", "")
    try:
        resp = requests.request(method, url, timeout=15)
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, TypeError):
            data = resp.text
        variables = {}
        if response_var:
            variables[response_var] = data
        return {"response": None, "next_node": None, "variables": variables,
                "actions": [{"type": "api_success", "data": data}]}
    except Exception as exc:
        return {"response": None, "next_node": None, "variables": {},
                "actions": [{"type": "api_error", "error": str(exc)}]}


def _exec_variable_set(node, ctx):
    config = node.get("config", {})
    variables = config.get("variables", {})
    result = {}
    for name, template in variables.items():
        result[name] = _interpolate(str(template), ctx)
    return {"response": None, "next_node": None, "variables": result, "actions": []}


def _exec_capture(node, ctx, query=None):
    config = node.get("config", {})
    variable_name = config.get("variable_name", "")
    prompt = _interpolate(config.get("prompt", ""), ctx)
    validation = config.get("validation", "")
    error_message = config.get("error_message", "Invalid input. Please try again.")
    if not query:
        return {"response": prompt, "next_node": None, "variables": {}, "actions": [{"type": "await_input"}]}
    if validation:
        try:
            if validation == "email":
                if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", query or ""):
                    return {"response": error_message, "next_node": None, "variables": {}, "actions": []}
            elif validation == "number":
                float(query)
            elif validation == "url":
                if not re.match(r"^https?://", query or ""):
                    return {"response": error_message, "next_node": None, "variables": {}, "actions": []}
            elif validation.startswith("regex:"):
                if not re.match(validation[6:], query or ""):
                    return {"response": error_message, "next_node": None, "variables": {}, "actions": []}
        except (ValueError, TypeError):
            return {"response": error_message, "next_node": None, "variables": {}, "actions": []}
    next_node = config.get("next_node")
    return {"response": None, "next_node": next_node, "variables": {variable_name: query}, "actions": []}


def _exec_handoff(node, ctx, conv_id=None, user=None):
    config = node.get("config", {})
    message = _interpolate(config.get("message", ""), ctx)
    reason = config.get("reason", "flow_handoff")
    if conv_id and user:
        try:
            from . import store as _store
            _store.update_flow_session  # noqa
        except Exception:
            pass
    return {"response": message, "next_node": None, "variables": {},
            "actions": [{"type": "handoff", "reason": reason}]}


# ------------------------------------------------------------------ engine

NODE_EXECUTORS = {
    "text": _exec_text,
    "choice": _exec_choice,
    "llm": _exec_llm,
    "condition": _exec_condition,
    "api_call": _exec_api_call,
    "variable_set": _exec_variable_set,
    "capture": _exec_capture,
    "handoff": _exec_handoff,
}


class FlowEngine:
    def __init__(self, app):
        self.app = app

    def start_session(self, flow_id, user_id, bot_id, conversation_id=None):
        session_id = store.create_flow_session(self.app, flow_id, user_id, bot_id, conversation_id)
        return store.get_flow_session(self.app, session_id)

    def get_session(self, session_id):
        return store.get_flow_session(self.app, session_id)

    def get_session_by_conversation(self, conversation_id):
        return store.get_flow_session_by_conversation(self.app, conversation_id)

    def advance(self, session_id, user_input=None):
        session = store.get_flow_session(self.app, session_id)
        if not session:
            return {"error": "Session not found", "status": "error"}

        flow_id = session["flow_id"]
        definition = store.get_published_flow(self.app, flow_id)
        if not definition:
            return {"error": "Flow not published", "status": "error"}

        nodes = {n["node_id"]: n for n in definition.get("nodes", [])}
        current_node_id = session.get("current_node_id") or ""

        if not current_node_id and nodes:
            current_node_id = list(nodes.keys())[0]

        session_vars = {}
        if isinstance(session.get("variables"), str):
            try:
                session_vars = json.loads(session["variables"] or "{}")
            except (ValueError, TypeError):
                session_vars = {}
        else:
            session_vars = dict(session.get("variables") or {})

        bot = store.get_bot(self.app, session["bot_id"])
        user = store.get_user(self.app, session["user_id"])
        ctx = _build_ctx(self.app, bot, user, session_vars)

        max_hops = 20
        response = None
        citations = None
        provider = None
        model = None
        actions = []
        final_node_id = current_node_id

        for _ in range(max_hops):
            if not current_node_id or current_node_id not in nodes:
                break
            node = nodes[current_node_id]
            node_type = node.get("type")
            executor = NODE_EXECUTORS.get(node_type)
            if not executor:
                break

            next_nodes = node.get("next_nodes") or []
            config = node.get("config", {})

            if node_type == "llm":
                result = executor(self.app, bot, user, node, ctx, query=user_input)
            elif node_type in ("choice", "capture"):
                result = executor(node, ctx, query=user_input)
            elif node_type == "condition":
                result = executor(node, ctx, session_vars)
            elif node_type == "handoff":
                conv_id = session.get("conversation_id")
                result = executor(node, ctx, conv_id=conv_id, user=user)
            else:
                result = executor(node, ctx)

            final_node_id = current_node_id

            if result.get("response"):
                response = result["response"]
                citations = result.get("citations")
                provider = result.get("provider")
                model = result.get("model")
                actions.extend(result.get("actions", []))

            if result.get("variables"):
                session_vars.update(result["variables"])

            next_node = result.get("next_node")
            if next_node and next_node in nodes:
                current_node_id = next_node
                next_type = nodes[next_node].get("type")
                if next_type in ("choice", "capture"):
                    break
                user_input = None
                continue
            elif next_nodes:
                current_node_id = next_nodes[0]
                next_type = nodes[current_node_id].get("type")
                if next_type in ("choice", "capture"):
                    break
                user_input = None
                continue
            else:
                current_node_id = None
                break

        store.update_flow_session(self.app, session_id,
            variables=session_vars,
            current_node_id=current_node_id or "",
        )

        status = session.get("status", "active")
        if any(a.get("type") == "handoff" for a in actions):
            status = "escalated"
            store.update_flow_session(self.app, session_id, status="escalated")

        return {
            "response": response,
            "next_node": current_node_id,
            "variables": session_vars,
            "citations": citations,
            "provider": provider or "flow",
            "model": model or "flow",
            "actions": actions,
            "status": status,
        }