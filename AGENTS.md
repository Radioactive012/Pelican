# Context Passport — Agent Operating Guide & Final Plan

> **MANDATORY FOR ALL AI AGENTS**
>
> This file is the single source of truth for product scope, version roadmap, safety boundaries,
> and technical architecture. All prior drafts, obsolete version plans (V0-V5), and discarded
> features (dashboards, MCP, Cursor integrations) are superseded by this plan.
> Do not declare a version complete until its required gates pass. V3 development is in progress; live browser verification is still pending.

---

## 1. Product Summary & Architecture

Context Passport is a **self-improving, privacy-first browser memory** product.

- **One compact extension UI**: Works natively on **ChatGPT (`chatgpt.com`)**, **Claude (`claude.ai`)**, and **Gemini web (`gemini.google.com`)**. Contains a compact side-panel vault and settings. Includes a universal side-panel copy/paste fallback route if a site changes its DOM.
- **One backend**: FastAPI with Mem0 OSS, verified Gemini Flash extraction model (`gemini-2.5-flash`), Gemini embedding model (`models/gemini-embedding-001` with 1536 dims), MongoDB Atlas, and Supabase Auth.
- **One application database**: **MongoDB Atlas stores both Mem0 vector memories and app-control collections** (`observations`, `preferences`, `dedup_events`). Supabase supplies **sign-in only** (JWT issuance/validation), eliminating dual-database coordination.
- **One narrow learning behavior**: Automatically learns **how the user likes answers explained**. Promotes an explanation-style preference only after **three distinct user-authored observations across at least two conversations**. User corrections lock the wording and take priority over later automatic guesses.
- **Privacy Firewall**: Memories are classified as:
  - `general`: eligible for automatic use when relevant (up to 3 general memories selected automatically by the backend).
  - `sensitive/uncertain`: requires an explicit **"Allow once"** or **"Don't use"** prompt before prompt preparation.
  - `secret`: recognizable credentials, API keys, passwords, and private tokens are screened and skipped entirely. Never stored or logged.

### Explicit Out-of-Scope Items
- No separate dashboard web app. Everything is managed inside the extension side panel.
- No MCP servers or MCP client adapters.
- No Cursor or coding-assistant extensions.
- No additional native websites beyond ChatGPT, Claude, and Gemini web.

---

## 2. Core Trust Boundaries & Privacy Rules

1. **Capture is OFF by default**: Starts disabled. The user enables it per site and can pause it at any time.
2. **User-authored messages only**: Only new, completed messages written by the user are capture candidates. Never capture assistant responses.
3. **No self-referential capture**: Extension-inserted memory blocks or previously injected context never count as new user evidence.
4. **Defense-in-depth secret screening**: Recognized secrets and credentials are screened in the extension before upload, and screened again on the backend. Recognizable credentials are completely skipped.
5. **No raw transcript retention**: The backend does not log message bodies or retain full conversation transcripts. Only distilled facts, explanation observations, and deduplication hashes are persisted.
6. **Strict tenant isolation**: All vector searches use `ScopedMongoDB` placing `payload.user_id` inside the `$vectorSearch.filter`. Identity is derived strictly from verified Supabase Auth JWTs, never from client-provided user IDs.
7. **Two-phase approval for sensitive context**: When "Use Memory" runs, general memories are attached automatically, but sensitive candidates require explicit user consent ("Allow once"). Denying consent excludes the sensitive memory while keeping the prompt ready.

---

## 3. Three-Version Plan & Verification Gates

### Version 1 — Working Memory and Learning Brain (Hours 0–6) — [IMPLEMENTED]

Build the FastAPI backend, MongoDB Atlas integration, Mem0 OSS tenant-scoped adapter, Gemini Flash extractor, Gemini embeddings, and Supabase Auth JWT verification.

#### Requirements:
- Backend accepts only authenticated, user-authored messages via Bearer token (Supabase Auth).
- Deduplication store (`dedup_events`) rejects duplicate events idempotently.
- Secret screener detects recognizable credentials and skips them.
- Mem0 + Gemini extracts durable facts and stores them in MongoDB with 1536-dimensional embeddings.
- Custom scoped MongoDB vector search enforces pre-filtered tenant isolation (`payload.user_id`).
- Narrow learning extractor records explanation-style evidence. Promotes an explanation preference **only after 3 distinct observations across at least 2 conversations**.
- 1 observation, or multiple observations in a single conversation, must NOT promote a preference.
- Memories classified as `general`, `sensitive/uncertain`, or `secret`.

#### Version 1 Passes When:
1. Manually supplied test messages create a searchable fact.
2. Differently worded search finds the fact.
3. Three observations across two chats create one evidence-backed preference.
4. One observation does not create a preference.
5. A fake credential is completely skipped.
6. Two accounts cannot see each other's memories.

---

### Version 2 — Three-Site Extension and Privacy Firewall (Hours 6–14) — [LIVE GATE PENDING]

Build the Manifest V3 browser extension with shared TypeScript logic and site adapters for `chatgpt.com`, `claude.ai`, and `gemini.google.com`.

#### Requirements:
- Per-site capture toggle (starts OFF).
- Site adapters identify completed user messages, conversation ID, and the native prompt composer.
- Client-side secret and sensitivity screener.
- On-page **"Use Memory"** button:
  - Automatically queries backend with draft prompt.
  - Automatically selects up to 3 relevant general memories.
  - Visibly injects approved context into the native composer.
  - Replaces earlier injected blocks cleanly if clicked repeatedly.
  - If a sensitive memory is relevant, prompts user with **"Allow once"** or **"Don't use"** before injecting.
- Single extension side-panel with compact vault, settings, and universal copy/paste fallback route.

#### Version 2 Passes When:
1. Capture OFF sends nothing.
2. Each of the three sites captures one user message without capturing an assistant reply.
3. A fact learned on ChatGPT is automatically selected by **Use Memory** on Claude and Gemini.
4. A sensitive candidate pauses for approval.
5. Denial keeps it out of the prepared prompt.
6. Repeated page updates do not duplicate memories.

---

### Version 3 — Feedback, Controls, and Finished Demo (Hours 14–18) — [IN DEVELOPMENT]

Complete the side-panel memory vault, controls, deletion lifecycles, and rehearse the judge demonstration.

#### Requirements:
- Vault displays cards: wording, short redacted supporting evidence, source, status, and classification.
- User can edit/correct, block, or forget any memory.
- Correcting an inferred explanation preference **locks the user's wording** so later model inferences cannot overwrite it.
- Removing supporting evidence triggers re-evaluation of the preference.
- Blocking immediately excludes a memory from retrieval.
- Forgetting deletes the Atlas record and prevents queued events from recreating it.
- Backend deployment verification and full judge journey rehearsal:
  - Make explanation-style requests across separate chats.
  - Show inferred preference with evidence in vault.
  - Use memory automatically in a different AI provider.
  - Trigger and deny a synthetic sensitive memory request.
  - Correct the preference, lock it, and show the locked wording applies across chats.

#### Version 3 Passes When:
1. Complete judge journey executes cleanly in a real browser session.
2. Correcting, blocking, forgetting, refresh/retry, and multi-tenant access do not leak or resurrect memories.

---

## 4. Current Pinned Compatibility Set

| Component | Pinned Version / Config | Purpose |
|---|---|---|
| Python | `3.11.15` | Backend runtime |
| Node.js | `>= 20.x` | Extension build & script runner |
| Mem0 OSS | `2.2.0` | Memory orchestration |
| Google Gen AI SDK | `2.25.0` | Gemini extraction & embeddings |
| Extraction Model | `gemini-2.5-flash` | Fact and preference extraction |
| Embedding Model | `models/gemini-embedding-001` | 1536-dimensional embeddings |
| MongoDB Driver | PyMongo `4.18.2` | MongoDB Atlas driver |
| Vector Index | `memories_vector_index_scoped` | Atlas Vector Search with `payload.user_id` filter |
| Backend Framework | FastAPI `0.141.1` + Uvicorn `0.37.0` | REST API |
| Auth Provider | Supabase Auth | User identity / JWT validation |

---

## 5. Development & Testing Discipline

1. **Test-first for each version**: Implement the code and the verification suite to prove all criteria before declaring completion.
2. **Never commit secrets**: `.env` is git-ignored and contains private API keys and database credentials.
3. **No scope creep**: Reject dashboards, extra websites, MCP, or complex extra settings. Keep the UI compact in the extension side panel.
