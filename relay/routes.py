"""Dashboard HTML routes."""
from __future__ import annotations

import json
import os
import time

from flask import (Blueprint, current_app, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from . import store
from .auth import current_user, login_required

bp = Blueprint("dash", __name__)


def ctx():
    app = current_app
    user = current_user(app)
    bots = store.list_bots(app, user["id"]) if user else []
    return app, user, bots


def active_bot(app, user, bots):
    if not bots:
        return None
    want = request.args.get("bot")
    if want:
        for b in bots:
            if str(b["id"]) == str(want) or b["slug"] == want:
                session["bot_id"] = b["id"]
                return b
    bid = session.get("bot_id")
    for b in bots:
        if b["id"] == bid:
            return b
    session["bot_id"] = bots[0]["id"]
    return bots[0]


@bp.route("/")
@login_required
def overview():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    stats = store.overview_stats(app, user["id"])
    recent = store.list_conversations(app, user["id"], bot_id=bot["id"] if bot else None, limit=6)
    try:
        from .rag import resolve_provider
        provider, _, model = resolve_provider(app, user)
    except Exception:
        provider, model = "offline", ""
    return render_template("overview.html", user=user, bots=bots, bot=bot, stats=stats,
                           recent=recent, provider=provider, model=model)


# ------------------------------------------------------------------ chatbots

@bp.route("/chatbots")
@login_required
def chatbots():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    return render_template("chatbots.html", user=user, bots=bots, bot=bot)


@bp.route("/chatbots/new", methods=["POST"])
@login_required
def chatbot_new():
    app, user, _ = ctx()
    name = request.form.get("name", "").strip() or "Untitled bot"
    description = request.form.get("description", "").strip()
    from .rag import resolve_provider
    provider, _, _ = resolve_provider(app, user)
    bot = store.create_bot(app, user["id"], name, description)
    session["bot_id"] = bot["id"]
    flash(f"Chatbot “{bot['name']}” created. Add knowledge, then test it in the Playground.", "ok")
    return redirect(url_for("dash.chatbot_edit", bot_id=bot["id"]))


@bp.route("/chatbots/<int:bot_id>", methods=["GET", "POST"])
@login_required
def chatbot_edit(bot_id):
    app, user, bots = ctx()
    bot = store.get_bot(app, bot_id, user["id"])
    if not bot:
        flash("Chatbot not found.", "error")
        return redirect(url_for("dash.chatbots"))
    session["bot_id"] = bot["id"]
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            store.delete_bot(app, bot_id, user["id"])
            session.pop("bot_id", None)
            flash("Chatbot deleted.", "ok")
            return redirect(url_for("dash.chatbots"))
        suggestions = [request.form.get(f"sugg{i}", "").strip() for i in range(4)]
        store.update_bot_suggestions(app, bot_id, user["id"], suggestions)
        store.update_bot(app, bot_id, user["id"],
                         name=request.form.get("name", bot["name"]).strip() or bot["name"],
                         description=request.form.get("description", ""),
                         color=request.form.get("color", "#4f46e5"),
                         avatar_emoji=request.form.get("avatar_emoji", ""),
                         greeting=request.form.get("greeting", ""),
                         model=request.form.get("model", ""),
                         temperature=request.form.get("temperature", 0.4),
                         system_prompt=request.form.get("system_prompt", ""),
                         ask_email=1 if request.form.get("ask_email") else 0,
                         active=0 if request.form.get("active") == "0" else 1)
        flash("Chatbot settings saved.", "ok")
        return redirect(url_for("dash.chatbot_edit", bot_id=bot_id))
    bot["chunks"] = store.count_chunks(app, bot["id"])
    try:
        from .rag import resolve_provider
        provider, _, model = resolve_provider(app, user)
    except Exception:
        provider, model = "offline", ""
    return render_template("bot_edit.html", user=user, bots=bots, bot=bot,
                           provider=provider, model=model)


# ------------------------------------------------------------------ sources

@bp.route("/sources")
@login_required
def sources():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    srcs = store.list_sources(app, bot["id"], user["id"]) if bot else []
    return render_template("sources.html", user=user, bots=bots, bot=bot, sources=srcs)


def _index_text_source(app, user_id, source_id, bot_id, text, label):
    from .rag import chunk_text
    chunks = chunk_text(text)
    if not chunks:
        store.mark_source(app, source_id, user_id, "error", error="No readable text found.")
        return 0
    store.replace_chunks(app, source_id, bot_id, chunks)
    store.mark_source(app, source_id, user_id, "ready",
                      detail=f"{len(text):,} characters, {len(chunks)} chunks")
    return len(chunks)


@bp.route("/sources/text", methods=["POST"])
@login_required
def source_text():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    name = request.form.get("name", "").strip() or "Pasted text"
    content = request.form.get("content", "").strip()
    if len(content) < 20:
        flash("Please paste at least a few sentences of knowledge text.", "error")
        return redirect(url_for("dash.sources"))
    sid = store.create_source(app, bot["id"], user["id"], name, "text",
                              status="processing", content=content)
    n = _index_text_source(app, user["id"], sid, bot["id"], content, name)
    flash(f"'{name}' indexed ({n} chunks)." if n else "Could not index that text.",
          "ok" if n else "error")
    return redirect(url_for("dash.sources"))


@bp.route("/sources/upload", methods=["POST"])
@login_required
def source_upload():
    from werkzeug.utils import secure_filename

    from .parsers import MAX_UPLOAD_BYTES, parse_uploaded_file
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a file first (PDF, DOCX, TXT, MD, CSV).", "error")
        return redirect(url_for("dash.sources"))
    fname = secure_filename(f.filename)
    dest = os.path.join(app.config["UPLOAD_DIR"], f"u{user['id']}b{bot['id']}_{int(time.time())}_{fname}")
    f.save(dest)
    if os.path.getsize(dest) > MAX_UPLOAD_BYTES:
        os.remove(dest)
        flash("File is larger than the 8 MB limit.", "error")
        return redirect(url_for("dash.sources"))
    sid = store.create_source(app, bot["id"], user["id"], fname, "file",
                              status="processing", file_path=dest)
    try:
        text = parse_uploaded_file(dest, fname)
        n = _index_text_source(app, user["id"], sid, bot["id"], text, fname)
        flash(f"'{fname}' indexed ({n} chunks)." if n else "No readable text in that file.",
              "ok" if n else "error")
    except Exception as exc:
        store.mark_source(app, sid, user["id"], "error", error=str(exc))
        flash(f"Could not read {fname}: {exc}", "error")
    return redirect(url_for("dash.sources"))


# ------------------------------------------------------ website + manage

@bp.route("/sources/website", methods=["POST"])
@login_required
def source_website():
    from .parsers import crawl_website
    from .rag import chunk_text
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    url = request.form.get("url", "").strip()
    try:
        max_pages = max(1, min(int(request.form.get("max_pages", 8) or 8), 25))
    except ValueError:
        max_pages = 8
    if not url:
        flash("Enter a website URL.", "error")
        return redirect(url_for("dash.sources"))
    name = request.form.get("name", "").strip() or url
    sid = store.create_source(app, bot["id"], user["id"], name, "website",
                              status="processing", url=url)
    try:
        pages = crawl_website(url, max_pages=max_pages)
        combined = "\n\n".join(f"# {p['title']}\nSource: {p['url']}\n\n{p['text'][:6000]}" for p in pages)
        chunks = chunk_text(combined)
        store.replace_chunks(app, sid, bot["id"], chunks)
        store.mark_source(app, sid, user["id"], "ready",
                          detail=f"{len(pages)} pages, {len(chunks)} chunks",
                          content=combined[:200000])
        flash(f"Crawled {len(pages)} pages into {len(chunks)} chunks.", "ok")
    except Exception as exc:
        store.mark_source(app, sid, user["id"], "error", error=str(exc))
        flash(f"Crawl failed: {exc}", "error")
    return redirect(url_for("dash.sources"))


@bp.route("/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def source_delete(source_id):
    app, user, _ = ctx()
    src = store.delete_source(app, source_id, user["id"])
    flash(f"Source '{src['name']}' deleted." if src else "Source not found.",
          "ok" if src else "error")
    return redirect(url_for("dash.sources"))


@bp.route("/sources/<int:source_id>/retry", methods=["POST"])
@login_required
def source_retry(source_id):
    from .parsers import crawl_website, parse_uploaded_file
    from .rag import chunk_text
    app, user, _ = ctx()
    src = store.get_source(app, source_id, user["id"])
    if not src:
        flash("Source not found.", "error")
        return redirect(url_for("dash.sources"))
    try:
        if src["type"] == "website" and src.get("url"):
            pages = crawl_website(src["url"], max_pages=8)
            combined = "\n\n".join(f"# {p['title']}\nSource: {p['url']}\n\n{p['text'][:6000]}" for p in pages)
            store.replace_chunks(app, src["id"], src["bot_id"], chunk_text(combined))
            store.mark_source(app, src["id"], user["id"], "ready",
                              detail=f"{len(pages)} pages", content=combined[:200000])
            flash(f"Re-crawled {len(pages)} pages.", "ok")
        elif src["type"] == "file" and src.get("file_path") and os.path.exists(src["file_path"]):
            text = parse_uploaded_file(src["file_path"], src["name"])
            n = _index_text_source(app, user["id"], src["id"], src["bot_id"], text, src["name"])
            flash(f"Re-indexed ({n} chunks).", "ok" if n else "error")
        elif src.get("content"):
            n = _index_text_source(app, user["id"], src["id"], src["bot_id"], src["content"], src["name"])
            flash(f"Re-indexed ({n} chunks).", "ok" if n else "error")
        else:
            flash("Nothing to re-index for this source.", "error")
    except Exception as exc:
        store.mark_source(app, src["id"], user["id"], "error", error=str(exc))
        flash(f"Re-index failed: {exc}", "error")
    return redirect(url_for("dash.sources"))


# ---------------------------------------------------------------- playground

@bp.route("/playground")
@login_required
def playground():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    try:
        from .rag import resolve_provider
        provider, _, model = resolve_provider(app, user)
    except Exception:
        provider, model = "offline", ""
    return render_template("playground.html", user=user, bots=bots, bot=bot,
                           provider=provider, model=model)


# ---------------------------------------------------- inbox & insights

@bp.route("/inbox")
@login_required
def inbox():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    status = request.args.get("status", "")
    convs = store.list_conversations(app, user["id"], bot_id=bot["id"] if bot else None,
                                     status=status or None, limit=100)
    sel_id = request.args.get("c", type=int)
    selected = None
    messages = []
    if sel_id:
        selected = store.get_conversation(app, sel_id, user["id"])
        if selected:
            messages = store.get_messages(app, sel_id, user["id"])
    elif convs:
        selected = store.get_conversation(app, convs[0]["id"], user["id"])
        messages = store.get_messages(app, convs[0]["id"], user["id"])
    for m in messages:
        try:
            m["citations"] = json.loads(m["citations"]) if isinstance(m["citations"], str) else (m["citations"] or [])
        except (ValueError, TypeError):
            m["citations"] = []
    if selected and not selected.get("title"):
        first = next((m["content"][:60] for m in messages if m["role"] == "user"), "Conversation")
        store.set_conversation(app, selected["id"], user["id"], title=first)
        selected["title"] = first
    return render_template("inbox.html", user=user, bots=bots, bot=bot, convs=convs,
                           selected=selected, messages=messages, status=status)


@bp.route("/inbox/<int:conv_id>/status", methods=["POST"])
@login_required
def inbox_status(conv_id):
    app, user, _ = ctx()
    store.set_conversation(app, conv_id, user["id"], status=request.form.get("status", "open"))
    return redirect(url_for("dash.inbox", c=conv_id))


@bp.route("/inbox/<int:conv_id>/delete", methods=["POST"])
@login_required
def inbox_delete(conv_id):
    app, user, _ = ctx()
    store.delete_conversation(app, conv_id, user["id"])
    flash("Conversation deleted.", "ok")
    return redirect(url_for("dash.inbox"))


@bp.route("/inbox/search")
@login_required
def inbox_search():
    """AJAX: search conversations by message text."""
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify(results=[])
    rows = store.search_conversations(app, user["id"], q, bot_id=bot["id"] if bot else None)
    return jsonify(results=[{"id": r["id"], "title": (r.get("title") or "Conversation")[:70],
                             "bot": r.get("bot_name", "")} for r in rows])


@bp.route("/inbox/export.csv")
@login_required
def inbox_export():
    from flask import Response
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    csv_text = store.export_conversations_csv(app, user["id"], bot_id=bot["id"] if bot else None)
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=relay-conversations.csv"})


@bp.route("/insights")
@login_required
def insights():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    bid = bot["id"] if bot else None
    daily = store.daily_counts(app, user["id"], bot_id=bid, days=14)
    top = store.top_questions(app, user["id"], bot_id=bid, limit=8)
    ratings = store.rating_summary(app, user["id"], bot_id=bid)
    return render_template("insights.html", user=user, bots=bots, bot=bot,
                           daily=daily, top=top, ratings=ratings,
                           daily_json=json.dumps(daily))


@bp.route("/contacts")
@login_required
def contacts():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    rows = store.list_contacts(app, user["id"], bot_id=bot["id"] if bot else None)
    return render_template("contacts.html", user=user, bots=bots, bot=bot, contacts=rows)


@bp.route("/contacts/<int:cid>/delete", methods=["POST"])
@login_required
def contact_delete(cid):
    app, user, _ = ctx()
    store.delete_contact(app, cid, user["id"])
    flash("Contact removed.", "ok")
    return redirect(url_for("dash.contacts"))


@bp.route("/contacts/export.csv")
@login_required
def contacts_export():
    from flask import Response
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    csv_text = store.export_contacts_csv(app, user["id"], bot_id=bot["id"] if bot else None)
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=relay-contacts.csv"})


@bp.route("/api/retrieval")
@login_required
def api_retrieval():
    """Retrieval inspector: show ranked chunks for a query (debugging data quality)."""
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    q = request.args.get("q", "").strip()
    if not bot or len(q) < 2:
        return jsonify(results=[])
    hits = store.debug_retrieval(app, bot["id"], q)
    return jsonify(results=[{"source": h["label"], "score": h["score"],
                             "preview": h["text"][:280]} for h in hits])


# ------------------------------------------------------ deploy & keys

@bp.route("/deploy")
@login_required
def deploy():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    keys = store.list_api_keys(app, user["id"])
    hooks = store.list_webhooks(app, user["id"])
    base = request.host_url.rstrip("/")
    return render_template("deploy.html", user=user, bots=bots, bot=bot,
                           keys=keys, hooks=hooks, base=base,
                           new_key=session.pop("new_key", None))


@bp.route("/deploy/keys/new", methods=["POST"])
@login_required
def key_new():
    app, user, _ = ctx()
    name = request.form.get("name", "").strip() or "Website key"
    _, raw = store.create_api_key(app, user["id"], name)
    session["new_key"] = raw
    flash("API key created — copy it now, it won't be shown again.", "ok")
    return redirect(url_for("dash.deploy"))


@bp.route("/deploy/keys/<int:kid>/delete", methods=["POST"])
@login_required
def key_delete(kid):
    app, user, _ = ctx()
    store.delete_api_key(app, kid, user["id"])
    flash("API key revoked.", "ok")
    return redirect(url_for("dash.deploy"))


@bp.route("/deploy/hooks/new", methods=["POST"])
@login_required
def hook_new():
    app, user, _ = ctx()
    url = request.form.get("url", "").strip()
    if url.startswith(("http://", "https://")):
        store.add_webhook(app, user["id"], url)
        flash("Webhook added.", "ok")
    else:
        flash("Enter a valid http(s) URL.", "error")
    return redirect(url_for("dash.deploy"))


@bp.route("/deploy/hooks/<int:wid>/delete", methods=["POST"])
@login_required
def hook_delete(wid):
    app, user, _ = ctx()
    store.delete_webhook(app, wid, user["id"])
    flash("Webhook removed.", "ok")
    return redirect(url_for("dash.deploy"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    app, user, bots = ctx()
    bot = active_bot(app, user, bots)
    if request.method == "POST":
        form = request.form.get("form", "profile")
        if form == "profile":
            store.update_user(app, user["id"], name=request.form.get("name", user["name"]).strip(),
                              workspace=request.form.get("workspace", user["workspace"]).strip())
            flash("Workspace settings saved.", "ok")
        elif form == "ai":
            provider = request.form.get("ai_provider", "auto")
            if provider not in ("auto", "offline", "openai", "anthropic"):
                provider = "auto"
            store.update_user(app, user["id"], ai_provider=provider,
                              ai_api_key=request.form.get("ai_api_key", "").strip(),
                              ai_model=request.form.get("ai_model", "").strip())
            flash("AI provider settings saved.", "ok")
        return redirect(url_for("dash.settings"))
    user = store.get_user(app, user["id"])
    try:
        from .rag import resolve_provider
        provider, _, model = resolve_provider(app, user)
    except Exception:
        provider, model = "offline", ""
    masked = ("..." + user["ai_api_key"][-4:]) if user.get("ai_api_key") else ""
    return render_template("settings.html", user=user, bots=bots, bot=bot,
                           provider=provider, model=model, masked=masked)


@bp.route("/api/health")
def health():
    return jsonify(ok=True)


@bp.route("/s/<slug>")
def share(slug):
    # Short share link -> canonical public chat page.
    return redirect(f"/chat/{slug}")
