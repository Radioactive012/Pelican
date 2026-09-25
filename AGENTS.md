# Context Passport — Multi-Agent Engineering Protocol & Operational Memory

> **MANDATORY FOR ALL AI AGENTS (Codex & Gemini / Antigravity)**  
> **READ THIS ENTIRE FILE BEFORE EXECUTING ANY COMMAND OR EDITING CODE.**  
> This file is a living, self-improving operational memory. Whenever you encounter a bug, fix an architectural flaw, or learn a critical constraint, you **MUST append it to the [Mistakes & Lessons Learned](#mistakes--lessons-learned-living-log) section** before concluding your turn.

---

## 1. Project Overview & Core Philosophy

**Context Passport** provides sovereign, portable AI memory and context isolation. Instead of re-explaining preferences, architecture, and coding styles across every assistant (Cursor, Claude, ChatGPT, etc.), Context Passport gives users a persistent memory passport with fine-grained firewalls.

### Current Phase: **V0 — Prove the Dependencies**
We do not build dashboards or browser extensions until the foundational integration gate passes:
$$\text{Conversation} \xrightarrow{\text{Gemini 2.5 Flash}} \text{Extracted Fact} \xrightarrow{\text{1536-dim Embedding}} \text{MongoDB Atlas} \xrightarrow{\text{Different Wording}} \text{Isolated Retrieval}$$
In parallel:
$$\text{Cursor Assistant} \xrightarrow{\text{Streamable HTTP MCP}} \text{Render Deployed Server} \xrightarrow{\text{Call Tool}} \text{Harmless Ping Marker}$$

---

## 2. Multi-Agent Protocol (Codex & Gemini / Antigravity)

The human user alternates between **Codex** and **Gemini (Antigravity)**. Both agents work on the same codebase. Follow these collaboration rules:

1. **Check Git Status First**: Always run `git status` before starting work to understand what the other agent just modified.
2. **Never Stomp on Unfinished Work**: If one agent is working on MongoDB or backend scripts, coordinate cleanly without reverting their changes.
3. **Keep Commits Clean & Documented**: Use conventional commits (`feat(gate): ...`, `fix(mongo): ...`) so the next agent has full context.
4. **Preserve Tested Patterns**: Do not introduce ad-hoc abstractions if a tested adapter (`ScopedMongoDB`, `MemoryManager`) already exists.
5. **Never Commit Secrets**: Credentials belong strictly in `.env`. Never commit `.env` or print secret keys to logs.

---

## 3. Pinned Runtime & Architecture Stack (2026)

All versions are strictly resolved and locked in `backend/requirements.lock` and `package-lock.json`:

| Component | Pinned Version | Purpose & Notes |
|---|---|---|
| **Python** | `3.11.15` | Tested and stable in `backend/.venv` |
| **Node.js** | `v24.13.0` | Root monorepo workspace coordinator |
| **Mem0 OSS** | `2.2.0` | Memory orchestration framework |
| **Google GenAI** | `2.25.0` | Official Google GenAI SDK |
| **Extraction Model** | `gemini-2.5-flash` | Stable extraction candidate |
| **Embedding Model** | `models/gemini-embedding-001` | **1536 dimensions** via Matryoshka output config |
| **Database** | `pymongo 4.18.2` | MongoDB Atlas with Vector Search |
| **FastAPI / MCP** | `0.141.1` / `mcp 2.2.0` | Official MCP Python SDK v2 Streamable HTTP |
| **Hosting** | Render Blueprint | `render.yaml` with Python 3.11 web service |

---

## 4. Verification Gates & Execution Commands

Every agent must verify their work using these commands before reporting completion:

```bash
# 1. Run all unit and regression tests (8 passing)
npm test

# 2. Audit installed Mem0 version behavior
npm run gate:audit

# 3. Prove Mem0 SQLite history lifecycle (delete/update retention)
npm run gate:history

# 4. Verify MongoDB Atlas Vector Index is READY and queryable
npm run gate:index

# 5. Direct Atlas Vector Search isolation check (no Gemini key needed)
PYTHONPATH=backend backend/.venv/bin/python backend/test_atlas_vector_isolation.py

# 6. Prove live Gemini extraction and 1536-dim embedding (requires GEMINI_API_KEY)
npm run gate:gemini

# 7. Prove Supabase Auth reachability & synthetic users
# Requires SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY
npm run gate:supabase

# 8. Full end-to-end memory ingestion and two-user semantic isolation proof
npm run gate:memory

# 9. Verify MCP Streamable HTTP protocol client call
npm run gate:mcp

# 10. Start local MCP server
npm start
```

### Current Integration Status (2026-09-25)

- **Supabase**: `context-passport` is provisioned on the Free plan in `ap-south-1` (Mumbai).
- **Local configuration**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are populated in the git-ignored `.env`. Never copy their values into source files, logs, commits, or this document.
- **Auth proof**: The health endpoint returns HTTP 200, and both synthetic users authenticate with confirmed email addresses. `npm run gate:supabase` passes.
- **Regression suite**: `npm test` passes with 8 tests.
- **Supabase hardening**: `backend/prove_supabase.py` implements an idempotent Admin API fallback (`/auth/v1/admin/users`) using `SUPABASE_SERVICE_ROLE_KEY` to provision and update confirmed synthetic users, avoiding rate limits. `npm run gate:supabase` passes reliably.
- **Next V0 sequence**: Populate `GEMINI_API_KEY`, run `gate:gemini` and `gate:memory`, then deploy to Render and test `gate:mcp` against the live endpoint.

---

## 5. Build Roadmap & Phases

- **V0 (Current)**: Real integration proof of all outside dependencies:
  - Gemini 2.5 Flash extraction + 1536-dim embedding.
  - MongoDB Atlas scoped vector search with user isolation.
  - Mem0 source audit & history persistence documentation.
  - Hosted Streamable HTTP MCP server on Render with Cursor calling `ping`.
- **V1 (Identity & Security Firewall)**:
  - Supabase Auth session validation.
  - Assistant client token authorization (Cursor / Claude / Web).
  - Memory firewall: filter out private tags, cross-tenant isolation enforcement.
- **V2 (Data Ingestion & Interfaces)**:
  - Browser extension capture for web assistants.
  - Web dashboard (`dashboard/`) for viewing, categorizing, and editing memories.
- **V3 (Hybrid Retrieval & Performance)**:
  - Reciprocal Rank Fusion (RRF) combining Atlas Vector Search + Atlas Lexical Text Search.
  - Sub-10ms query optimization using dedicated M10 Atlas cluster.
- **V4 (True Forgetting & Compliance)**:
  - Hard-delete engine: purging both MongoDB vectors AND SQLite/relational history rows.
  - GDPR export and selective forgetting controls.

---

## 6. Mistakes & Lessons Learned (Living Log)

*(Every agent must add newly discovered gotchas and fixes to this list!)*

1. **Mem0 MongoDB Adapter Post-Filtering Flaw**:
   - *Problem*: Mem0's default `MongoDB.search()` inserts `$match` *after* `$vectorSearch`. In multi-tenant databases, Atlas selects the top-k nearest neighbors globally, and then filters for user_id. If other users have similar vectors, the target user gets 0 results (tenant starvation).
   - *Fix*: Context Passport uses `ScopedMongoDB` (`scoped_mongodb.py`), which places the identity filter directly inside `$vectorSearch.filter: {"payload.user_id": {"$eq": user_id}}`.

2. **Mem0 Missing Ownership Checks on Mutations**:
   - *Problem*: `Memory.update()`, `Memory.delete()`, and `Memory.history()` only accept a `memory_id` without verifying who owns it. An attacker could delete or modify another user's memory with a known ID.
   - *Fix*: `MemoryManager` implements `_assert_owner(memory_id, user_id)` before calling any Mem0 mutation method.

3. **Mem0 Deletion Leaves Plaintext Text in History Store**:
   - *Problem*: Deleting a memory via `vector_store.delete()` purges the vector, but Mem0's `SQLiteManager` appends a record with `event='DELETE', is_deleted=1`, keeping the previous text permanently in the `history` table.
   - *Lesson*: In V0/V1 we acknowledge this limitation. In V4, a hard-delete purge routine must delete rows from the history database.

4. **MongoDB Atlas TLS Alert Internal Error**:
   - *Problem*: Connecting to MongoDB Atlas failed with `[SSL: TLSV1_ALERT_INTERNAL_ERROR]`.
   - *Lesson*: This is MongoDB Atlas terminating the TLS handshake because the client's public IP is not in the Network Access IP Access List. Atlas must have `0.0.0.0/0` active for development and cloud deployments (Render).

5. **Decouple Database Diagnostics from LLM Keys**:
   - *Problem*: `verify_atlas_index.py` crashed because `load_settings()` strictly required `GEMINI_API_KEY` even when only verifying MongoDB.
   - *Fix*: `load_settings(require_gemini=False)` allows database and index diagnostic tools to run independently before the LLM key is configured.

6. **Gemini Embedding Dimension Configuration**:
   - *Problem*: Older tutorials use `text-embedding-004` which was deprecated in early 2026.
   - *Fix*: Use `models/gemini-embedding-001` configured with `output_dimensionality=1536` to match Atlas HNSW vector index dimensions.

7. **MongoDB Atlas Vector Search Asynchronous Ingestion Delay**:
   - *Problem*: Inserting a document into MongoDB Atlas writes to the collection immediately, but Atlas's underlying Lucene vector indexing pipeline updates asynchronously (typically taking 1-4 seconds). Running `$vectorSearch` immediately after insert causes queries to return empty results.
   - *Fix*: In automated verification gates (`test_atlas_vector_isolation.py`, `test_isolation.py`) and immediate write-then-read tests, poll `$vectorSearch` with a short retry loop (up to 15s) rather than asserting on an instantaneous single query.

8. **Fresh Supabase Projects Can Rate-Limit Synthetic Sign-Ups**:
   - *Problem*: Creating synthetic users through the public `/auth/v1/signup` endpoint can trigger Supabase's confirmation-email rate limit, especially when using non-routable test domains such as `.local`.
   - *Fix*: When `SUPABASE_SERVICE_ROLE_KEY` is available, provision synthetic users through `/auth/v1/admin/users` with `email_confirm: true`. This avoids sending test email while preserving email-confirmation requirements for normal public sign-ups.
