"""JSON API: dashboard chat, public widget, iframe chat, REST."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from . import chat as engine
from . import store
from .auth import current_user

bp = Blueprint("api", __name__)


def _dashboard_bot(bot_id):
    user = current_user(current_app)
    if not user:
        return None, None, jsonify(error="Login required"), 401
    bot = store.get_bot(current_app, bot_id, user["id"])
    if not bot:
        return None, None, jsonify(error="Bot not found"), 404
    return user, bot, None


# ------------------------------------------------------- dashboard chat

@bp.route("/api/bots/<int:bot_id>/chat", methods=["POST"])
def api_chat(bot_id):
    user, bot, err = _dashboard_bot(bot_id)
    if err:
        return user, err.status_code if isinstance(err, int) else err
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    conv_id = data.get("conversation_id")
    if not message:
        return jsonify(error="Empty message"), 400
    if not bot.get("active", 1):
        return jsonify(error="This bot is paused."), 403
    conv = store.get_conversation(current_app, conv_id, user["id"]) if conv_id else None
    if not conv or conv["bot_id"] != bot["id"]:
        conv_id = store.create_conversation(current_app, bot["id"], user["id"], channel="Playground")
        conv = store.get_conversation(current_app, conv_id)
    prior = [{"role": m["role"], "content": m["content"]}
             for m in store.get_messages(current_app, conv_id, user["id"])]
    store.add_message(current_app, conv_id, "user", message)
    reply = engine.answer_turn(current_app, bot, user, prior, message, conv_id=conv_id)
    mid = store.add_message(current_app, conv_id, "assistant", reply["text"],
                            citations=reply["citations"])
    if not conv.get("title"):
        store.set_conversation(current_app, conv_id, user["id"], title=message[:60])
    return jsonify(conversation_id=conv_id, message_id=mid, reply=reply["text"],
                   citations=reply["citations"], provider=reply["provider"], model=reply["model"],
                   guardrail=reply.get("guardrail"), guardrail_reason=reply.get("guardrail_reason"))


@bp.route("/api/bots/<int:bot_id>/conversations", methods=["POST"])
def api_new_conversation(bot_id):
    user, bot, err = _dashboard_bot(bot_id)
    if err:
        return user
    conv_id = store.create_conversation(current_app, bot["id"], user["id"], channel="Playground")
    return jsonify(conversation_id=conv_id,
                   greeting=bot.get("greeting") or "",
                   suggestions=bot.get("suggestions") or [])


# ------------------------------------------------------ message feedback

@bp.route("/api/messages/<int:mid>/rate", methods=["POST"])
def api_rate(mid):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    data = request.get_json(force=True, silent=True) or {}
    rating = 1 if int(data.get("rating", 1)) > 0 else -1
    store.rate_message(current_app, mid, user["id"], rating)
    return jsonify(ok=True)


@bp.route("/api/inbox/<int:conv_id>/reply", methods=["POST"])
def api_agent_reply(conv_id):
    """Owner sends a manual reply inside a conversation (shows in widget too)."""
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    conv = store.get_conversation(current_app, conv_id, user["id"])
    if not conv:
        return jsonify(error="Not found"), 404
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("message") or "").strip()
    if not text:
        return jsonify(error="Empty message"), 400
    mid = store.add_message(current_app, conv_id, "agent", text)
    return jsonify(ok=True, message_id=mid)


# ------------------------------------------------------ public endpoints

# ------------------------------------------------- widget, embed, rest

@bp.route("/api/public/config")
def api_public_config():
    slug = request.args.get("bot", "")
    bot = store.get_bot_by_slug(current_app, slug)
    if not bot or not bot.get("active", 1):
        return jsonify(error="Bot unavailable"), 404
    owner = store.get_user(current_app, bot["user_id"])
    return jsonify(name=bot["name"], color=bot["color"], avatar=bot.get("avatar_emoji") or "",
                   greeting=(bot.get("greeting") or f"Hi! How can I help you today?"),
                   suggestions=bot.get("suggestions") or [],
                   need_email=bool(bot.get("ask_email", 1)),
                   provider=engine.rag.resolve_provider(current_app, owner)[0] if owner else "offline")


@bp.route("/api/public/chat", methods=["POST"])
def api_public_chat():
    data = request.get_json(force=True, silent=True) or {}
    bot = store.get_bot_by_slug(current_app, data.get("bot", ""))
    if not bot or not bot.get("active", 1):
        return jsonify(error="Bot unavailable"), 404
    owner = store.get_user(current_app, bot["user_id"])
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify(error="Empty message"), 400
    visitor = (data.get("visitor_key") or "")[:64]
    name, email = (data.get("name") or "").strip()[:80], (data.get("email") or "").strip()[:120]
    conv = None
    if data.get("conversation_id"):
        conv = store.get_conversation(current_app, data["conversation_id"])
        if conv and (conv["bot_id"] != bot["id"] or (visitor and conv["visitor_key"] != visitor)):
            conv = None
    if not conv:
        conv_id = store.create_conversation(current_app, bot["id"], bot["user_id"],
                                            channel="Website", visitor_key=visitor, name=name, email=email)
    else:
        conv_id = conv["id"]
        if (name or email) and not conv.get("email"):
            store.set_conversation(current_app, conv_id, bot["user_id"], **{
                k: v for k, v in {"name": name, "email": email}.items() if v})
    prior = [{"role": m["role"], "content": m["content"]}
             for m in store.get_messages(current_app, conv_id)]
    store.add_message(current_app, conv_id, "user", message)
    low = message.lower()
    if "refund" in low and ("request" in low or "want" in low or "need" in low or "get" in low):
        engine.handle_refund_request(current_app, conv_id, bot, owner or {"id": bot["user_id"]})
    if email and "@" in email:
        store.upsert_contact(current_app, bot["id"], bot["user_id"], name=name, email=email)
    if not store.get_conversation(current_app, conv_id).get("title"):
        store.set_conversation(current_app, conv_id, bot["user_id"], title=message[:60])
    reply = engine.answer_turn(current_app, bot, owner or {"id": bot["user_id"]}, prior, message, conv_id=conv_id)
    mid = store.add_message(current_app, conv_id, "assistant", reply["text"], citations=reply["citations"])
    return jsonify(conversation_id=conv_id, message_id=mid, reply=reply["text"],
                   citations=reply["citations"], guardrail=reply.get("guardrail"),
                   guardrail_reason=reply.get("guardrail_reason"))


@bp.route("/api/public/history")
def api_public_history():
    try:
        conv_id = int(request.args.get("conversation_id", 0))
    except ValueError:
        return jsonify(messages=[])
    conv = store.get_conversation(current_app, conv_id)
    if not conv or conv["visitor_key"] != (request.args.get("visitor_key", "")[:64]):
        return jsonify(messages=[])
    msgs = [{"role": m["role"], "content": m["content"]} for m in store.get_messages(current_app, conv_id)]
    return jsonify(messages=msgs)


@bp.route("/api/rest/chat", methods=["POST"])
def api_rest_chat():
    """Programmatic REST: Authorization: Bearer rb-... + {bot, message}."""
    auth = request.headers.get("Authorization", "")
    key = store.verify_api_key(current_app, auth[7:] if auth.startswith("Bearer ") else "")
    if not key:
        return jsonify(error="Invalid API key"), 401
    data = request.get_json(force=True, silent=True) or {}
    bot = None
    if data.get("bot"):
        bot = store.get_bot_by_slug(current_app, data["bot"])
        if bot and bot["user_id"] != key["user_id"]:
            bot = None
    if not bot:
        return jsonify(error="Bot not found"), 404
    owner = store.get_user(current_app, key["user_id"])
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify(error="Empty message"), 400
    conv_id = store.create_conversation(current_app, bot["id"], key["user_id"],
                                        channel="API", name=data.get("name", ""),
                                        email=data.get("email", ""))
    store.add_message(current_app, conv_id, "user", message)
    reply = engine.answer_turn(current_app, bot, owner, [], message, conv_id=conv_id)
    store.add_message(current_app, conv_id, "assistant", reply["text"], citations=reply["citations"])
    return jsonify(conversation_id=conv_id, reply=reply["text"],
                   citations=reply["citations"], provider=reply["provider"],
                   guardrail=reply.get("guardrail"), guardrail_reason=reply.get("guardrail_reason"))


@bp.route("/widget.js")
def widget_js():
    from flask import Response
    js = render_template("widget.js", base=request.host_url.rstrip("/"))
    return Response(js, mimetype="application/javascript")


@bp.route("/chat/<slug>")
def public_chat(slug):
    bot = store.get_bot_by_slug(current_app, slug)
    if not bot or not bot.get("active", 1):
        from flask import abort
        abort(404)
    return render_template("chat_public.html", bot=bot)


# ------------------------------------------------------------------ flow API

@bp.route("/api/flows", methods=["GET"])
def api_list_flows():
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    flows = store.list_flows(current_app, user["id"])
    return jsonify(flows=flows)


@bp.route("/api/flows", methods=["POST"])
def api_create_flow():
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    if not name:
        return jsonify(error="Name required"), 400
    fid = store.create_flow(current_app, user["id"], name, description)
    return jsonify(flow_id=fid), 201


@bp.route("/api/flows/<int:flow_id>", methods=["GET"])
def api_get_flow(flow_id):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    flow = store.get_flow(current_app, flow_id, user["id"])
    if not flow:
        return jsonify(error="Flow not found"), 404
    nodes = store.get_flow_nodes(current_app, flow_id)
    definition = store.get_published_flow(current_app, flow_id)
    return jsonify(flow=flow, nodes=nodes, published=definition)


@bp.route("/api/flows/<int:flow_id>", methods=["PUT"])
def api_update_flow(flow_id):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    flow = store.get_flow(current_app, flow_id, user["id"])
    if not flow:
        return jsonify(error="Flow not found"), 404
    data = request.get_json(force=True, silent=True) or {}
    if "name" in data:
        store.update_flow(current_app, flow_id, user["id"], name=data["name"])
    if "description" in data:
        store.update_flow(current_app, flow_id, user["id"], description=data["description"])
    if "nodes" in data:
        store.replace_flow_nodes(current_app, flow_id, data["nodes"])
    return jsonify(ok=True)


@bp.route("/api/flows/<int:flow_id>/publish", methods=["POST"])
def api_publish_flow(flow_id):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    ver = store.publish_flow(current_app, flow_id, user["id"])
    if not ver:
        return jsonify(error="Flow not found"), 404
    return jsonify(version=ver, published=True)


@bp.route("/api/flows/<int:flow_id>/assign", methods=["POST"])
def api_assign_flow(flow_id):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    data = request.get_json(force=True, silent=True) or {}
    bot_id = data.get("bot_id")
    if not bot_id:
        return jsonify(error="bot_id required"), 400
    bot = store.get_bot(current_app, bot_id, user["id"])
    if not bot:
        return jsonify(error="Bot not found"), 404
    store.update_bot(current_app, bot_id, user["id"], flow={"flow_id": flow_id})
    return jsonify(ok=True)


@bp.route("/api/flow-sessions/<int:session_id>/advance", methods=["POST"])
def api_advance_flow_session(session_id):
    user = current_user(current_app)
    if not user:
        return jsonify(error="Login required"), 401
    session = store.get_flow_session(current_app, session_id)
    if not session or session["user_id"] != user["id"]:
        return jsonify(error="Session not found"), 404
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "")
    from .flow_engine import FlowEngine
    engine = FlowEngine(current_app)
    result = engine.advance(session_id, message)
    if "error" in result:
        return jsonify(error=result["error"]), 400
    # Persist assistant response as a message if there's a conversation
    conv_id = session.get("conversation_id")
    if conv_id and result.get("response"):
        bot = store.get_bot(current_app, session["bot_id"])
        store.add_message(current_app, conv_id, "assistant", result["response"],
                          citations=result.get("citations"))
    return jsonify(result)


# __API3__
