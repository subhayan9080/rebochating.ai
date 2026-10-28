# REA Findings — Relay (rebochating.ai) Architecture Map

Reverse-engineered from source code review. This document maps the existing
codebase internals and identifies extension points for adding Voiceflow-like
conversation flow features.

---

## 1. Project Structure

```
rebochating.ai/
├── app.py                    # Flask dev server entry point
├── requirements.txt          # flask, pypdf, python-docx, requests, beautifulsoup4
├── smoke_test.py             # ~40 end-to-end checks
├── .env.example              # FLASK_SECRET, OPENAI_API_KEY, ANTHROPIC_API_KEY
├── .gitignore
├── relay/                    # Flask package
│   ├── __init__.py           # App factory, blueprint registration
│   ├── auth.py               # Session auth: register/login/logout
│   ├── db.py                 # SQLite schema, init_db() with migrations
│   ├── store.py              # All data access layer
│   ├── chat.py               # Chat answering engine
│   ├── rag.py                # BM25 retrieval, chunking, LLM answers
│   ├── parsers.py            # File parsing + website crawling
│   ├── api.py                # JSON API: chat, public widget, REST
│   └── routes.py             # Dashboard HTML routes
├── static/css/               # base.css, app.css, chat.css, pro.css
├── static/js/                # app.js, pro.js
└── templates/                # Jinja2 HTML (layout.html, settings.html, etc.)

---

## 2. Data Model (SQLite Schema)

### Tables

| Table | Key Columns | Purpose |
|---|---|---|
| `users` | id, email, name, password_hash, workspace, ai_provider, ai_api_key, ai_model, theme, created_at | User accounts + AI provider settings |
| `bots` | id, user_id, name, description, slug, color, avatar_emoji, greeting, suggestions, model, temperature, system_prompt, ask_email, active, guardrails, flow, created_at, updated_at | Chatbot configs; `guardrails` and `flow` are JSON TEXT |
| `sources` | id, bot_id, user_id, name, type, status, url, file_path, detail, content, error, created_at | Knowledge sources (text, file, website) |
| `chunks` | id, source_id, bot_id, ord, text | Indexed text chunks for RAG |
| `conversations` | id, bot_id, user_id, visitor_key, name, email, channel, status, title, satisfaction, created_at, updated_at | Chat sessions; `flow_node` and `flow_ctx` added via migration |
| `messages` | id, conversation_id, role, content, rating, citations, created_at | Chat messages (user/assistant) |
| `contacts` | id, bot_id, user_id, name, email, company, phone, consent, created_at | Lead capture |
| `api_keys` | id, user_id, name, key, prefix, created_at, last_used | REST API keys (`rb-...` prefix) |
| `webhooks` | id, user_id, url, active, created_at | Outgoing webhook URLs |

### Migrations (in `init_db()`)
Adds columns post-release if missing:
- `users.theme`
- `bots.guardrails`, `bots.flow`
- `conversations.flow_node`, `conversations.flow_ctx`, `conversations.intent`

### JSON Columns (deserialized by `row_to_dict()`)
- `bots.suggestions`, `bots.guardrails`, `bots.flow`
- `messages.citations`
- `conversations.flow_ctx`

---

## 3. Auth & Session Model

- **Session cookie:** `session["uid"]` set on login/register
- **Password hashing:** `werkzeug.security.generate_password_hash` / `check_password_hash`
- **`login_required` decorator:** Redirects to `/login?next=<path>` if no `uid` in session
- **`current_user(app)`:** Looks up user by `session["uid"]` via `store.get_user()`
- **API keys:** `Bearer rb-...` token verified via `store.verify_api_key()` on `/api/rest/chat`
- **Public endpoints:** `/chat/<slug>`, `/api/public/config`, `/api/public/chat`, `/widget.js` (no auth)

---

## 4. Chat / RAG Pipeline (Current Flow)

```
User message
    │
    ▼
POST /api/bots/<id>/chat   (api.py:api_chat)
    │
    ▼
store.get_messages()        # prior conversation history
    │
    ▼
rag.load_bot_index(bot_id)  # loads cached BM25 index from chunks
rag.bm25_search(query, k=4) # top-k chunk retrieval
    │
    ▼
chat.evaluate_guardrails()  # keyword escalation + confidence threshold
    │
    ├─► escalate -> status="needs_human", fire webhook, return guardrail text
    ├─► fallback -> return fallback message
    └─► continue
    │
    ▼
provider == "offline"?
    ├─ YES -> rag.offline_answer()  (extractive from chunks, no API)
    └─ NO  -> rag.llm_answer()      (OpenAI/Anthropic API with context)
    │
    ▼
store.add_message(assistant, text, citations)
    │
    ▼
JSON reply: {conversation_id, message_id, reply, citations, provider, model, guardrail, guardrail_reason}
```

### RAG Details
- **Chunking:** Paragraph-aware sliding window (~1100 chars, 180 overlap)
- **Retrieval:** BM25 with custom stemmer, stop-word removal, synonym hints
- **Offline answers:** Section-aware extractive snippet selection
- **LLM answers:** OpenAI `/v1/chat/completions` or Anthropic `/v1/messages`

---

## 5. Guardrails (Voiceflow-style safety, already present)

Defined in `relay/chat.py`:
- `confidence_threshold` — min BM25 score (0 = off)
- `fallback_message` — shown when confidence too low
- `escalate_keywords` — keywords that trigger human handoff (e.g. "refund")
- `escalation_message` — shown when escalating

Configured in `bot_edit.html` form; stored as JSON in `bots.guardrails`.

---

## 6. Extension Points for Voiceflow Features

| Location | Extension Point | Use For |
|---|---|---|
| `bots.flow` column | Already exists as JSON TEXT in schema | Store flow definition reference |
| `conversations.flow_node` / `flow_ctx` | Already migrated | Track active flow node + session variables per conversation |
| `store.update_bot(..., flow=...)` | Already accepts `flow` field | Assign flow to bot |
| `chat.answer_turn()` | Entry point for all chat | Intercept and route through flow engine if bot has flow |
| `chat.evaluate_guardrails()` | Guardrail evaluation | Wrap flow node execution |
| `rag.resolve_provider()` | LLM provider resolution | Call from `llm` node type |
| `api.py` blueprint | JSON API routes | Add `/api/flows/*`, `/api/flow-sessions/*` |
| `routes.py` blueprint | HTML dashboard routes | Add `/flows`, `/flows/<id>/edit` |
| `smoke_test.py` | Regression test suite | Add flow engine tests |

---

## 7. Files to Modify (Implementation Roadmap)

1. `relay/db.py` — Add `flows`, `flow_nodes`, `flow_versions`, `flow_sessions`, `intents` tables + migrations
2. `relay/store.py` — CRUD for flows, nodes, versions, sessions, intents
3. `relay/flow_engine.py` (NEW) — Node type registry, executor, variable interpolation
4. `relay/chat.py` — Route `answer_turn()` through flow engine when bot has flow
5. `relay/api.py` — Flow API endpoints (list, create, execute, publish)
6. `relay/routes.py` — Flow builder HTML routes
7. `templates/` — Flow builder canvas, flow list, node config modals
8. `static/css/pro.css` — Flow builder UI styles
9. `static/js/` — Flow builder canvas JS (drag-and-drop, connections)
10. `smoke_test.py` — Flow engine regression tests

---
