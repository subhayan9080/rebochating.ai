# Flow Design — Voiceflow-Like Conversation Builder for Relay

Design document for adding a visual conversation flow engine to the Relay
platform (rebochating.ai). Builds on the REA findings in `REA_FINDINGS.md`.

---

## 1. Goals

- Drag-and-drop conversation flow builder (like Voiceflow's flow canvas)
- Node types: text, choice, LLM, condition, API call, variable set, capture, handoff
- Session variables with interpolation (`{{user.name}}`, `{{session.email}}`)
- Intent classification to route between flow branches
- Flow versioning (draft/published)
- Reuse existing RAG, guardrails, LLM provider, and webhook infrastructure
- Zero new Python dependencies (stdlib + existing Flask stack)

---

## 2. New Database Tables

Added to `relay/db.py` SCHEMA and `init_db()` migrations:

### `flows`
```sql
CREATE TABLE IF NOT EXISTS flows(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  version INTEGER DEFAULT 1,
  is_published INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
```

### `flow_nodes`
```sql
CREATE TABLE IF NOT EXISTS flow_nodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id INTEGER NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  type TEXT NOT NULL,
  config TEXT DEFAULT '{}',
  position_x REAL DEFAULT 0,
  position_y REAL DEFAULT 0,
  next_nodes TEXT DEFAULT '[]',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flow_nodes_flow ON flow_nodes(flow_id);

### `flow_versions`
```sql
CREATE TABLE IF NOT EXISTS flow_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id INTEGER NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  definition TEXT NOT NULL,
  is_published INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_flow_versions_flow_ver ON flow_versions(flow_id, version);
```

### `flow_sessions`
```sql
CREATE TABLE IF NOT EXISTS flow_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id INTEGER NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  conversation_id INTEGER REFERENCES conversations(id),
  variables TEXT DEFAULT '{}',
  current_node_id TEXT DEFAULT '',
  status TEXT DEFAULT 'active',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flow_sessions_conv ON flow_sessions(conversation_id);
```

### `intents`
```sql
CREATE TABLE IF NOT EXISTS intents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  phrases TEXT DEFAULT '[]',
  responses TEXT DEFAULT '[]',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_bot ON intents(bot_id);
```

---

## 3. Node Type Registry

Each node type has an `execute()` function with signature:

```python
def execute(ctx: dict, node: dict, session_vars: dict) -> dict
```

Returns: `{"response": str|None, "next_node": str|None, "variables": dict, "actions": list}`

### Node Types

| Type | Config Fields | Behavior |
|---|---|---|
| `text` | `message` | Send static text to user |
| `choice` | `options: [{label, next_node, intent}]` | Present buttons; branch on selection or intent match |
| `llm` | `prompt`, `model_override`, `use_knowledge` | Generate AI response via RAG + LLM |
| `condition` | `rules: [{variable, operator, value, next_node}]` | Evaluate variable expressions, branch |
| `api_call` | `url`, `method`, `headers`, `body_template`, `response_var` | Call external API, store result |
| `variable_set` | `variables: {name: value_template}` | Set session variables |
| `capture` | `variable_name`, `prompt`, `validation`, `error_message` | Collect user input into variable |
| `handoff` | `message`, `reason` | Escalate to human agent (`needs_human`) |

### Default Node Execution Order

1. Find flow's `start_node` (first node or node marked as entry)
2. Execute node → get response + next_node
3. If `next_node` is set, continue to that node (for non-input nodes)
4. If node requires user input (`choice`, `capture`), wait for next message
5. On next user message, resume at `current_node_id`
```

---

## 4. Flow Execution Model

### `relay/flow_engine.py`

```python
class FlowEngine:
    def __init__(self, app):
        self.app = app
        self.node_types = {
            "text": TextNode,
            "choice": ChoiceNode,
            "llm": LlmNode,
            "condition": ConditionNode,
            "api_call": ApiCallNode,
            "variable_set": VariableSetNode,
            "capture": CaptureNode,
            "handoff": HandoffNode,
        }

    def start_session(self, flow_id, user_id, bot_id, conversation_id=None) -> dict
    def advance(self, session_id, user_input=None) -> dict
    def get_definition(self, flow_id) -> dict
    def match_intent(self, query, bot_id) -> dict|None
```

### Variable Interpolation

- `{{user.name}}` — from users table
- `{{user.email}}` — from users table
- `{{session.email}}` — from flow_sessions variables
- `{{session.any_var}}` — any captured variable
- `{{bot.name}}` — from bots table

Fallback: if variable missing, render as empty string.

### Intent Matching

- Keyword/regex matching against `intents.phrases`
- Returns `{name, confidence}` where confidence = matched phrase count / total phrases
- Confidence threshold configurable (default 0.3)

---

## 5. Integration with Existing Chat

### `relay/chat.py` — `answer_turn()` modification

```python
def answer_turn(app, bot, user, history, query, conv_id=None):
    # NEW: Check if bot has a published flow
    flow = get_flow_for_bot(app, bot["id"])
    if flow and flow["is_published"]:
        session = get_or_create_flow_session(app, flow, user, bot, conv_id)
        result = flow_engine.advance(session["id"], query)
        if result.get("response"):
            store.add_message(app, conv_id, "assistant", result["response"])
            return format_reply(result, provider="flow", model="flow")
        # If flow returned no response (waiting for input), continue below

    # EXISTING: RAG + LLM path
    ...
```

### Flow nodes that delegate to existing infrastructure

- **`llm` node:** Calls `rag.load_bot_index()` + `rag.bm25_search()` + `rag.llm_answer()` or `rag.offline_answer()`
- **`handoff` node:** Calls `chat.handle_refund_request()` / `store.set_conversation(status="needs_human")` + `store.fire_webhooks()`
- **`api_call` node:** Uses `requests` (already a dependency) to call external URL

---

## 6. API Endpoints

### `relay/api.py` additions

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/flows` | GET | List user's flows |
| `/api/flows` | POST | Create new flow (draft) |
| `/api/flows/<id>` | GET | Get flow definition |
| `/api/flows/<id>` | PUT | Update flow definition |
| `/api/flows/<id>/publish` | POST | Publish flow, increment version |
| `/api/flows/<id>/versions` | GET | List versions |
| `/api/flows/<id>/assign` | POST | Assign flow to a bot |
| `/api/flow-sessions/<id>/advance` | POST | Send user input, advance flow |

### Request/Response Example (advance)

**Request:**
```json
POST /api/flow-sessions/123/advance
{"message": "I want a refund"}
```

**Response:**
```json
{
  "response": "I've flagged this for a human agent who will follow up shortly.",
  "next_node": null,
  "variables": {},
  "actions": [{"type": "escalate", "reason": "keyword:refund"}],
  "status": "escalated"
}
```

---

## 7. Frontend Routes

### `relay/routes.py` additions

| Route | Purpose |
|---|---|
| `/flows` | List user's flows |
| `/flows/new` | Create new flow, redirect to editor |
| `/flows/<id>/edit` | Visual flow builder canvas |
| `/flows/<id>/publish` | Publish flow version |

---

## 8. Frontend Canvas (Vanilla JS)

No build step. Uses existing static file serving.

### Canvas Features
- Node palette (drag node types onto canvas)
- Draggable node cards with input/output ports
- SVG connection lines between ports
- Node configuration modal (per node type)
- JSON import/export
- Publish button (creates version, sets is_published)

### Reuse Existing Styles
- `data-theme` light/dark from `layout.html`
- `.card`, `.btn`, `.btn-primary`, `.field`, `.badge` from `base.css`/`app.css`
- SVG sprite icons from `layout.html`

---

## 9. Testing Plan

Extend `smoke_test.py` with:

1. Create flow → add text node → publish → assign to bot → chat returns flow response
2. Create flow with `choice` node → select option → branches to correct next node
3. Create flow with `capture` node → submit email → variable stored in session
4. Create flow with `handoff` node → conversation status = `needs_human`, webhook fired
5. Intent matching triggers flow transition
6. Existing non-flow bots still answer via RAG/LLM (regression)

---

## 10. Implementation Order

1. **Phase 3A:** `relay/db.py` — new tables + migrations
2. **Phase 3B:** `relay/store.py` — CRUD functions
3. **Phase 3C:** `relay/flow_engine.py` — node executor
4. **Phase 3D:** `relay/chat.py` — integrate flow into `answer_turn()`
5. **Phase 3E:** `relay/api.py` + `relay/routes.py` — API + HTML routes
6. **Phase 3F:** `templates/` + `static/` — flow builder UI
7. **Phase 3G:** `smoke_test.py` — flow tests

---
