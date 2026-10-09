"""All SQLite access. Functions take the Flask app (uses app.config['DB_PATH'])."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3

from .db import connect, now, row_to_dict, rows_to_dicts

DEFAULT_SYSTEM = (
    "You are {name}, a helpful customer-support assistant for {company}. "
    "Answer ONLY from the KNOWLEDGE BASE provided. Be concise and friendly. "
    "If the answer is not in the knowledge base, say so honestly and suggest "
    "contacting support. Never invent prices, policies, or links."
)


def _db(app) -> sqlite3.Connection:
    return connect(app.config["DB_PATH"])


def _one(app, sql, args=()):
    conn = _db(app)
    try:
        return row_to_dict(conn.execute(sql, args).fetchone())
    finally:
        conn.close()


def _all(app, sql, args=()):
    conn = _db(app)
    try:
        return rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def _write(app, sql, args=()):
    conn = _db(app)
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _exec(app, sql, args=()):
    conn = _db(app)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "bot").lower()).strip("-") or "bot"
    return slug[:40]


# ------------------------------------------------------------------ users

def create_user(app, name, email, pw_hash, workspace="My workspace"):
    return _write(app, "INSERT INTO users(name,email,password_hash,workspace,created_at)"
                  " VALUES(?,?,?,?,?)", (name, email.lower().strip(), pw_hash, workspace, now()))


def get_user_by_email(app, email):
    return _one(app, "SELECT * FROM users WHERE email=?", (email.lower().strip(),))


def get_user(app, uid):
    return _one(app, "SELECT * FROM users WHERE id=?", (uid,))


def update_user(app, uid, **fields):
    allowed = {"name", "workspace", "ai_provider", "ai_api_key", "ai_model"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    vals = [fields[k] for k in fields if k in allowed]
    _exec(app, f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, uid))


# ------------------------------------------------------------------ bots

def list_bots(app, user_id):
    rows = _all(app, "SELECT b.*, (SELECT COUNT(*) FROM sources s WHERE s.bot_id=b.id AND s.status='ready') AS source_count,"
                " (SELECT COUNT(*) FROM conversations c WHERE c.bot_id=b.id) AS conv_count"
                " FROM bots b WHERE b.user_id=? ORDER BY b.updated_at DESC", (user_id,))
    for r in rows:
        try:
            r["chunks"] = count_chunks(app, r["id"])
        except Exception:
            r["chunks"] = 0
    return rows


def get_bot(app, bot_id, user_id=None):
    if user_id is None:
        return _one(app, "SELECT * FROM bots WHERE id=?", (bot_id,))
    return _one(app, "SELECT * FROM bots WHERE id=? AND user_id=?", (bot_id, user_id))


def get_bot_by_slug(app, slug):
    return _one(app, "SELECT * FROM bots WHERE slug=?", (slug,))


def unique_slug(app, base):
    slug, i = slugify(base), 1
    while get_bot_by_slug(app, slug):
        i += 1
        slug = f"{slugify(base)}-{i}"
    return slug


def create_bot(app, user_id, name, description="", **kw):
    slug = unique_slug(app, name)
    suggestions = kw.get("suggestions") or ["What can you help with?", "How do I get started?", "Talk to a human"]
    if isinstance(suggestions, str):
        suggestions = [s.strip() for s in suggestions.splitlines() if s.strip()][:4] or suggestions
    bid = _write(app, """INSERT INTO bots(user_id,name,description,slug,color,avatar_emoji,greeting,
                suggestions,model,temperature,system_prompt,ask_email,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (user_id, name, description, slug, kw.get("color", "#4f46e5"),
                  kw.get("avatar_emoji", ""), kw.get("greeting", "") or "",
                  json.dumps(suggestions), kw.get("model", ""), float(kw.get("temperature", 0.4) or 0.4),
                  kw.get("system_prompt", "") or "", int(bool(kw.get("ask_email", True))), now(), now()))
    seed_bot_content(app, bid, user_id, name)
    return get_bot(app, bid)


def update_bot(app, bot_id, user_id, **fields):
    allowed = {"name", "description", "color", "avatar_emoji", "greeting", "model",
               "temperature", "system_prompt", "ask_email", "active"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "temperature":
            v = max(0.0, min(1.5, float(v or 0)))
        if k in ("ask_email", "active"):
            v = int(bool(v))
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(now())
    _exec(app, f"UPDATE bots SET {', '.join(sets)} WHERE id=? AND user_id=?", (*vals, bot_id, user_id))
    from .rag import invalidate_bot_index
    invalidate_bot_index(bot_id)


def update_bot_suggestions(app, bot_id, user_id, suggestions):
    sugg = [s.strip() for s in (suggestions or []) if s.strip()][:4]
    _exec(app, "UPDATE bots SET suggestions=?, updated_at=? WHERE id=? AND user_id=?",
          (json.dumps(sugg), now(), bot_id, user_id))


def delete_bot(app, bot_id, user_id):
    _exec(app, "DELETE FROM bots WHERE id=? AND user_id=?", (bot_id, user_id))
    from .rag import invalidate_bot_index
    invalidate_bot_index(bot_id)


def seed_bot_content(app, bot_id, user_id, bot_name):
    """Seed a starter 'Getting started' source so playground works instantly."""
    from .rag import chunk_text, invalidate_bot_index
    if _one(app, "SELECT id FROM sources WHERE bot_id=?", (bot_id,)):
        return
    content = (f"# Welcome to {bot_name}\n\n"
               f"{bot_name} is your AI support assistant. Ask it anything about your product.\n\n"
               "## How this works\n\n"
               "- Add knowledge in Data sources: paste text, upload files (PDF, DOCX, TXT, CSV), or crawl your website.\n"
               "- Test answers in the Playground.\n"
               "- Share the bot with a public link or embed the chat widget on your site.\n\n"
               "## Tips\n\n"
               "- Write clear FAQs; the bot answers only from what it has learned.\n"
               "- Use the approval queue for sensitive requests like refunds.\n")
    sid = _write(app, "INSERT INTO sources(bot_id,user_id,name,type,status,detail,content,created_at)"
                 " VALUES(?,?,?,?,?,?,?,?)",
                 (bot_id, user_id, "Getting started guide", "text", "ready",
                  f"{len(content):,} characters · built-in", content, now()))
    chunks = chunk_text(content)
    conn = _db(app)
    try:
        conn.executemany("INSERT INTO chunks(source_id,bot_id,ord,text) VALUES(?,?,?,?)",
                         [(sid, bot_id, i, c) for i, c in enumerate(chunks)])
        conn.commit()
    finally:
        conn.close()
    invalidate_bot_index(bot_id)


# --------------------------------------------------------------- sources

def list_sources(app, bot_id, user_id):
    return _all(app, "SELECT s.*, (SELECT COUNT(*) FROM chunks c WHERE c.source_id=s.id) AS chunk_count"
                " FROM sources s WHERE s.bot_id=? AND s.user_id=? ORDER BY s.created_at DESC",
                (bot_id, user_id))


def get_source(app, source_id, user_id):
    return _one(app, "SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user_id))


def create_source(app, bot_id, user_id, name, stype, status="processing", url="", file_path="", detail="", content=""):
    return _write(app, "INSERT INTO sources(bot_id,user_id,name,type,status,url,file_path,detail,content,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (bot_id, user_id, name, stype, status, url, file_path, detail, content, now()))


def mark_source(app, source_id, user_id, status, detail="", content=None, error=""):
    if content is None:
        _exec(app, "UPDATE sources SET status=?, detail=?, error=? WHERE id=? AND user_id=?",
              (status, detail, error, source_id, user_id))
    else:
        _exec(app, "UPDATE sources SET status=?, detail=?, error=?, content=? WHERE id=? AND user_id=?",
              (status, detail, error, content, source_id, user_id))


def delete_source(app, source_id, user_id):
    src = get_source(app, source_id, user_id)
    if not src:
        return None
    conn = _db(app)
    try:
        conn.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id=? AND user_id=?", (source_id, user_id))
        conn.commit()
    finally:
        conn.close()
    if src.get("file_path"):
        try:
            import os
            if os.path.exists(src["file_path"]):
                os.remove(src["file_path"])
        except OSError:
            pass
    from .rag import invalidate_bot_index
    invalidate_bot_index(src["bot_id"])
    return src


def replace_chunks(app, source_id, bot_id, chunks):
    conn = _db(app)
    try:
        conn.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        conn.executemany("INSERT INTO chunks(source_id,bot_id,ord,text) VALUES(?,?,?,?)",
                         [(source_id, bot_id, i, c) for i, c in enumerate(chunks)])
        conn.commit()
    finally:
        conn.close()
    from .rag import invalidate_bot_index
    invalidate_bot_index(bot_id)


def count_chunks(app, bot_id):
    row = _one(app, "SELECT COUNT(*) AS n FROM chunks WHERE bot_id=?", (bot_id,))
    return (row or {}).get("n", 0)


def bot_chunks(app, bot_id):
    rows = _all(app, "SELECT c.id, c.text, s.name AS source FROM chunks c"
                " JOIN sources s ON s.id=c.source_id"
                " WHERE c.bot_id=? AND s.status='ready' ORDER BY c.source_id, c.ord", (bot_id,))
    return [{"id": r["id"], "text": r["text"], "label": r.get("source") or "Knowledge"} for r in rows]


def bot_chunks_stamp(app, bot_id):
    row = _one(app, "SELECT COUNT(*) AS n, COALESCE(MAX(c.id),0) AS m FROM chunks c"
               " JOIN sources s ON s.id=c.source_id WHERE c.bot_id=? AND s.status='ready'", (bot_id,))
    row = row or {"n": 0, "m": 0}
    return f"{row.get('n', 0)}:{row.get('m', 0)}"


# ------------------------------------------------------ conversations

def create_conversation(app, bot_id, user_id, channel="Playground", visitor_key="", name="", email=""):
    return _write(app, "INSERT INTO conversations(bot_id,user_id,visitor_key,name,email,channel,created_at,updated_at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (bot_id, user_id, visitor_key, name, email, channel, now(), now()))


def get_conversation(app, conv_id, user_id=None):
    if user_id is None:
        return _one(app, "SELECT * FROM conversations WHERE id=?", (conv_id,))
    return _one(app, "SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))


def list_conversations(app, user_id, bot_id=None, status=None, limit=100):
    sql = ("SELECT c.*, b.name AS bot_name,"
           " (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count,"
           " (SELECT m.content FROM messages m WHERE m.conversation_id=c.id AND m.role='user'"
           "  ORDER BY m.id LIMIT 1) AS first_msg"
           " FROM conversations c JOIN bots b ON b.id=c.bot_id WHERE c.user_id=?")
    args = [user_id]
    if bot_id:
        sql += " AND c.bot_id=?"
        args.append(bot_id)
    if status:
        sql += " AND c.status=?"
        args.append(status)
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    return _all(app, sql, args)


def set_conversation(app, conv_id, user_id, **fields):
    allowed = {"status", "satisfaction", "name", "email", "title"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    vals = [fields[k] for k in fields if k in allowed]
    sets.append("updated_at=?")
    vals.append(now())
    _exec(app, f"UPDATE conversations SET {', '.join(sets)} WHERE id=? AND user_id=?", (*vals, conv_id, user_id))


def touch_conversation(app, conv_id):
    _exec(app, "UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))


def add_message(app, conv_id, role, content, citations=None, rating=0):
    mid = _write(app, "INSERT INTO messages(conversation_id,role,content,rating,citations,created_at)"
                 " VALUES(?,?,?,?,?,?)",
                 (conv_id, role, content, rating, json.dumps(citations or []), now()))
    touch_conversation(app, conv_id)
    return mid


def get_messages(app, conv_id, user_id=None):
    if user_id is not None and not get_conversation(app, conv_id, user_id):
        return []
    return _all(app, "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))


def rate_message(app, message_id, user_id, rating):
    _exec(app, "UPDATE messages SET rating=? WHERE id=? AND conversation_id IN"
          " (SELECT id FROM conversations WHERE user_id=?)", (rating, message_id, user_id))


def delete_conversation(app, conv_id, user_id):
    conn = _db(app)
    try:
        conn.execute("DELETE FROM messages WHERE conversation_id IN"
                     " (SELECT id FROM conversations WHERE id=? AND user_id=?)", (conv_id, user_id))
        conn.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------- contacts

def upsert_contact(app, bot_id, user_id, name="", email="", company="", phone=""):
    email = (email or "").strip().lower()
    if email:
        existing = _one(app, "SELECT * FROM contacts WHERE bot_id=? AND email=?", (bot_id, email))
        if existing:
            _exec(app, "UPDATE contacts SET name=?, company=?, phone=? WHERE id=?",
                  (name or existing["name"], company or existing["company"], phone or existing["phone"], existing["id"]))
            return existing["id"]
    return _write(app, "INSERT INTO contacts(bot_id,user_id,name,email,company,phone,created_at)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (bot_id, user_id, name, email, company, phone, now()))


def list_contacts(app, user_id, bot_id=None, limit=500):
    if bot_id:
        return _all(app, "SELECT c.*, b.name AS bot_name FROM contacts c JOIN bots b ON b.id=c.bot_id"
                    " WHERE c.user_id=? AND c.bot_id=? ORDER BY c.created_at DESC LIMIT ?",
                    (user_id, bot_id, limit))
    return _all(app, "SELECT c.*, b.name AS bot_name FROM contacts c JOIN bots b ON b.id=c.bot_id"
                " WHERE c.user_id=? ORDER BY c.created_at DESC LIMIT ?", (user_id, limit))


def delete_contact(app, contact_id, user_id):
    _exec(app, "DELETE FROM contacts WHERE id=? AND user_id=?", (contact_id, user_id))


# ---------------------------------------------------- keys & webhooks

def create_api_key(app, user_id, name):
    raw = "rb-" + secrets.token_urlsafe(24)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    kid = _write(app, "INSERT INTO api_keys(user_id,name,key,prefix,created_at) VALUES(?,?,?,?,?)",
                 (user_id, name, digest, raw[:8] + "...", now()))
    return kid, raw


def list_api_keys(app, user_id):
    return _all(app, "SELECT id,name,prefix,created_at,last_used FROM api_keys WHERE user_id=? ORDER BY id DESC",
                (user_id,))


def verify_api_key(app, raw):
    digest = hashlib.sha256((raw or "").encode()).hexdigest()
    row = _one(app, "SELECT * FROM api_keys WHERE key=?", (digest,))
    if row:
        _exec(app, "UPDATE api_keys SET last_used=? WHERE id=?", (now(), row["id"]))
    return row


def delete_api_key(app, key_id, user_id):
    _exec(app, "DELETE FROM api_keys WHERE id=? AND user_id=?", (key_id, user_id))


def list_webhooks(app, user_id):
    return _all(app, "SELECT * FROM webhooks WHERE user_id=? ORDER BY id DESC", (user_id,))


def add_webhook(app, user_id, url):
    return _write(app, "INSERT INTO webhooks(user_id,url,created_at) VALUES(?,?,?)", (user_id, url, now()))


def delete_webhook(app, wid, user_id):
    _exec(app, "DELETE FROM webhooks WHERE id=? AND user_id=?", (wid, user_id))


def fire_webhooks(app, user_id, event, payload):
    hooks = [h for h in list_webhooks(app, user_id) if h["active"]]
    if not hooks:
        return 0
    import threading

    import requests

    def _send(url):
        try:
            requests.post(url, json={"event": event, **payload}, timeout=8)
        except Exception:
            pass

    for h in hooks:
        threading.Thread(target=_send, args=(h["url"],), daemon=True).start()
    return len(hooks)


# ------------------------------------------------------------------ stats

def overview_stats(app, user_id):
    bots = _all(app, "SELECT id FROM bots WHERE user_id=?", (user_id,))
    bids = [b["id"] for b in bots]
    if not bids:
        return {"bots": 0, "conversations": 0, "messages": 0, "contacts": 0, "sources": 0, "chunks": 0}
    ph = ",".join("?" * len(bids))
    conv = _one(app, f"SELECT COUNT(*) n FROM conversations WHERE user_id=? AND bot_id IN ({ph})", (user_id, *bids))
    msg = _one(app, "SELECT COUNT(*) n FROM messages WHERE conversation_id IN"
               f" (SELECT id FROM conversations WHERE user_id=? AND bot_id IN ({ph}))", (user_id, *bids))
    con = _one(app, f"SELECT COUNT(*) n FROM contacts WHERE user_id=? AND bot_id IN ({ph})", (user_id, *bids))
    src = _one(app, f"SELECT COUNT(*) n FROM sources WHERE user_id=? AND bot_id IN ({ph}) AND status='ready'",
               (user_id, *bids))
    chk = _one(app, f"SELECT COUNT(*) n FROM chunks WHERE bot_id IN ({ph})", (*bids,))
    return {"bots": len(bids), "conversations": conv["n"], "messages": msg["n"],
            "contacts": con["n"], "sources": src["n"], "chunks": chk["n"]}


def _day_label(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%b %d")


def daily_counts(app, user_id, bot_id=None, days=14):
    since = now() - days * 86400
    args = [user_id, since]
    filt = ""
    if bot_id:
        filt = " AND c.bot_id=?"
        args.append(bot_id)
    rows = _all(app, f"""SELECT CAST(m.created_at/86400 AS INTEGER)*86400 AS day, COUNT(*) AS n
        FROM messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE c.user_id=? AND m.created_at>=?{filt} GROUP BY day ORDER BY day""", args)
    out = [{"day": r["day"], "label": _day_label(r["day"]), "messages": r["n"]} for r in rows]
    cargs = [user_id, since] + ([bot_id] if bot_id else [])
    cfilt = " AND bot_id=?" if bot_id else ""
    rows2 = _all(app, "SELECT CAST(created_at/86400 AS INTEGER)*86400 AS day, COUNT(*) AS n"
                 f" FROM conversations WHERE user_id=? AND created_at>=?{cfilt} GROUP BY day ORDER BY day", cargs)
    cmap = {r["day"]: r["n"] for r in rows2}
    for d in out:
        d["conversations"] = cmap.get(d["day"], 0)
    return out


def top_questions(app, user_id, bot_id=None, limit=8):
    args = [user_id]
    filt = ""
    if bot_id:
        filt = " AND c.bot_id=?"
        args.append(bot_id)
    args.append(limit)
    return _all(app, f"""SELECT m.content AS q, COUNT(*) AS n FROM messages m
        JOIN conversations c ON c.id=m.conversation_id
        WHERE c.user_id=? AND m.role='user'{filt} GROUP BY m.content ORDER BY n DESC LIMIT ?""", args)


def rating_summary(app, user_id, bot_id=None):
    args = [user_id]
    filt = ""
    if bot_id:
        filt = " AND c.bot_id=?"
        args.append(bot_id)
    rows = _all(app, f"""SELECT m.rating AS r, COUNT(*) AS n FROM messages m
        JOIN conversations c ON c.id=m.conversation_id
        WHERE c.user_id=? AND m.role='assistant' AND m.rating != 0{filt}
        GROUP BY m.rating""", args)
    return {str(r["r"]): r["n"] for r in rows}


# ---------------------------------------------------- exports & search

def export_conversations_csv(app, user_id, bot_id=None):
    """Return CSV text of conversations + messages (for download)."""
    import csv
    import io
    convs = list_conversations(app, user_id, bot_id=bot_id, limit=2000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conversation_id", "bot", "channel", "status", "visitor", "email",
                "role", "message", "rating", "sent_at"])
    for c in convs:
        for m in get_messages(app, c["id"]):
            import datetime
            w.writerow([c["id"], c.get("bot_name", ""), c.get("channel", ""), c.get("status", ""),
                        c.get("name", ""), c.get("email", ""), m["role"],
                        (m["content"] or "").replace("\n", " "), m.get("rating", 0),
                        datetime.datetime.fromtimestamp(m["created_at"]).strftime("%Y-%m-%d %H:%M")])
    return buf.getvalue()


def export_contacts_csv(app, user_id, bot_id=None):
    import csv
    import io
    rows = list_contacts(app, user_id, bot_id=bot_id, limit=5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "company", "phone", "bot", "captured_at"])
    for c in rows:
        import datetime
        w.writerow([c.get("name", ""), c.get("email", ""), c.get("company", ""),
                    c.get("phone", ""), c.get("bot_name", ""),
                    datetime.datetime.fromtimestamp(c["created_at"]).strftime("%Y-%m-%d %H:%M")])
    return buf.getvalue()


def search_conversations(app, user_id, query, bot_id=None, limit=30):
    """Full-text-ish search over user messages."""
    like = f"%{query}%"
    sql = ("SELECT c.*, b.name AS bot_name FROM conversations c JOIN bots b ON b.id=c.bot_id"
           " WHERE c.user_id=? AND c.id IN (SELECT conversation_id FROM messages"
           " WHERE role='user' AND content LIKE ?)")
    args = [user_id, like]
    if bot_id:
        sql += " AND c.bot_id=?"
        args.append(bot_id)
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    return _all(app, sql, args)


def debug_retrieval(app, bot_id, query):
    """Return ranked chunks for a query (powers the retrieval inspector)."""
    from .rag import bm25_search, load_bot_index
    index = load_bot_index(app, bot_id)
    return bm25_search(index, query, k=8)


# ------------------------------------------------------------- demo seed

DEMO_BOT_NAME = "Acme Support"
DEMO_FAQ = """# Acme -- Frequently asked questions

## Pricing
Starter is $49/month for up to 3 seats. Business is $149/month for up to 10 seats.
Enterprise plans are custom -- talk to sales for a quote. Annual billing saves 20%.

## Refunds
Refund requests need a human review. We review within 2 business days and issue
refunds to the original payment method. The assistant can collect order details
and open a review request, but it never promises or issues a refund itself.

## Getting started
Create a workspace, invite teammates from Settings, then connect your help center
or upload docs so the bot can learn. Embed the widget with one script tag, or
share the public chat link.

## Support hours
Live support is available Monday-Friday, 9am-6pm ET. The AI assistant answers
instantly, 24/7, from your knowledge base.

## Security
Encryption in transit and at rest, SSO on Business plans, and EU data residency
on Enterprise. Ask your account manager for the latest SOC 2 report.
"""


def ensure_demo(app, user_id):
    """Seed one demo bot with FAQ content for brand-new accounts."""
    if _one(app, "SELECT id FROM bots WHERE user_id=?", (user_id,)):
        return
    bot = create_bot(app, user_id, DEMO_BOT_NAME,
                     description="Demo assistant trained on sample Acme content.",
                     avatar_emoji="🤖",
                     greeting="Hi there! I'm the Acme support assistant. How can I help?")
    update_bot_suggestions(app, bot["id"], user_id,
                           ["What are your prices?", "What is the refund policy?", "How do I get started?"])
    from .rag import chunk_text, invalidate_bot_index
    sid = create_source(app, bot["id"], user_id, "Acme FAQ", "text", status="ready",
                        detail="Built-in demo content", content=DEMO_FAQ)
    replace_chunks(app, sid, bot["id"], chunk_text(DEMO_FAQ))
    invalidate_bot_index(bot["id"])


def rate_message_public(app, message_id, conv_id, rating):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return False
    if rating not in (-1, 1):
        return False
    conn = connect(app.config["DB_PATH"])
    try:
        cur = conn.execute("UPDATE messages SET rating=? WHERE id=? AND conversation_id=?",
                           (rating, message_id, conv_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
