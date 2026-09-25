# Context Passport V3

Context Passport is an opt-in, self-improving, privacy-first browser memory extension for **ChatGPT** (`chatgpt.com`), **Claude** (`claude.ai`), and **Gemini Web** (`gemini.google.com`).

With capture enabled, it distills useful facts and learns your explanation style preferences from user-authored messages. On each supported site, a **Use Memory** button inspects your current prompt draft, automatically selects relevant general memories (up to 3), prompts for sensitive candidates via a two-phase Privacy Firewall, and injects structured context directly into the chat prompt.

---

## 1. System Architecture & Boundaries

- **Single Extension UI (`v3/dist`)**: Built with TypeScript for Chrome and Brave (Manifest V3). Features inline prompt injection, a compact side-panel vault with evidence inspection and lifecycle controls (Correct, Block, Forget), and a universal copy/paste fallback route.
- **Strict Localhost Runtime**: FastAPI backend bound strictly to `127.0.0.1:8000` (`http://localhost:8000`). No public network exposure (`0.0.0.0`), no Render, no Fly.io, and no cloud server hosting.
- **Single Application Database**: MongoDB Atlas stores Mem0 vector memories and all app-control collections (`observations`, `preferences`, `dedup_events`, `forgotten_preferences`, `forgotten_memory_sources`).
- **Supabase Auth for Sign-In Only**: Supabase supplies JWT token issuance and validation. No dual-database synchronization; identity is derived solely from verified tokens (`payload.user_id`).
- **Provider-Aware Model Routing**:
  - Primary Fact Extractor: OpenRouter GLM 5.3 Flash (`openrouter/glm-5.3-flash`).
  - Fallback Fact Extractor: Gemini 2.5 Flash Lite via OpenRouter (`google/gemini-2.5-flash-lite`), activated automatically upon 429 rate limit, 5xx error, or provider timeout.
  - Embedder: `openai/text-embedding-3-small` (1536 dimensions) via OpenRouter.
  - Validation Pass: Feature-flagged Jev from TypeSafe AI (`ENABLE_JEV_VALIDATION=false` by default).
- **Privacy Firewall**:
  - `general`: Automatically eligible for prompt injection (up to 3 general memories).
  - `sensitive`: Demoted to two-phase approval modal (**Allow once** vs **Don't use**).
  - `secret`: API keys, bearer tokens, and credentials are screened and skipped client-side and backend; never stored, never embedded.
- **Narrow Learning Behavior**: Automatically learns only explanation style. Promotes preferences only after **3 distinct observations across at least 2 distinct conversations**. User corrections lock the wording and prevent automatic AI overwrites.

---

## 2. Prerequisites

1. **Python 3.11.x**:
   Verify with `python3.11 --version`.
2. **Node.js >= 20.x**:
   Verify with `node --version`.
3. **MongoDB Atlas Account**:
   A free MongoDB Atlas cluster (M0 or higher).
4. **Supabase Account**:
   A Supabase project with Email Auth enabled (Authentication -> Providers -> Email).
5. **OpenRouter API Key** *(for live extraction/embeddings at Step 10; mock runs use synthetic providers)*.

---

## 3. Environment Configuration

Copy the template to `.env` in the repository root:

```bash
cp .env.example .env
```

Open `.env` and configure the following variables (do NOT commit `.env` to git):

| Environment Variable | Description | Default / Example |
|---|---|---|
| `LLM_PROVIDER` | Extractor provider (`openrouter` or `gemini`) | `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter API authentication key | `sk-or-v1-...` |
| `OPENROUTER_BASE_URL` | OpenRouter API base endpoint | `https://openrouter.ai/api/v1` |
| `EXTRACTION_PRIMARY_MODEL` | Primary LLM fact extraction model | `z-ai/glm-5.3-flash` |
| `EXTRACTION_FALLBACK_MODEL` | Fallback LLM fact extraction model | `google/gemini-2.5-flash-lite` |
| `EMBEDDING_PROVIDER` | Embeddings provider (`openrouter`) | `openrouter` |
| `EMBEDDING_MODEL` | 1536-dimensional embedding model | `openai/text-embedding-3-small` |
| `EMBEDDING_DIMS` | Vector embedding dimension count | `1536` |
| `ENABLE_JEV_VALIDATION` | Enable Jev (TypeSafe AI) validation pass | `false` |
| `JEV_API_KEY` | TypeSafe AI API key (if Jev enabled) | |
| `OPENROUTER_SPENDING_CAP` | Hard spending limit guard in USD | `1.00` |
| `PROVIDER_TIMEOUT_SECONDS` | Provider HTTP timeout in seconds | `30.0` |
| `PROVIDER_MAX_RETRIES` | Max retries before triggering fallback | `2` |
| `MONGODB_URI` | MongoDB Atlas connection string | `mongodb+srv://user:pass@cluster.mongodb.net/...` |
| `MONGODB_DB_NAME` | Application database name | `context_passport` |
| `MONGODB_COLLECTION_NAME` | Vector memories collection | `memories` |
| `MONGODB_VECTOR_INDEX_NAME` | Atlas Vector Search index name | `memories_vector_index_scoped` |
| `SUPABASE_URL` | Supabase project API URL | `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | Supabase project anonymous public key | `eyJhbGciOi...` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (for backend auth verification) | `eyJhbGciOi...` |
| `HOST` | Backend listener IP (strictly loopback) | `127.0.0.1` |
| `PORT` | Backend listener port | `8000` |
| `MEM0_HISTORY_DB_PATH` | Local SQLite path for Mem0 history | `backend/mem0_history.db` |

### MongoDB Atlas Vector Search Index Setup
In MongoDB Atlas, go to **Atlas Search & Vector Search** on the `memories` collection in database `context_passport` and create a Vector Search Index named `memories_vector_index_scoped`:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "payload.user_id"
    }
  ]
}
```

---

## 4. Installation & Build

### Step 1: Backend Setup
Create and activate a Python 3.11 virtual environment, then install dependencies:

```bash
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
```

### Step 2: Extension Setup & Build
Install Node dependencies from the repository root:

```bash
npm install
npm run build:v3
```

This compiles TypeScript source from `v3/src/` into the clean extension artifact directory: `v3/dist/`.
> **Note**: `v3/dist` is the **only** extension artifact for Context Passport V3.

---

## 5. Running the Backend

Start the local loopback backend from the repository root:

```bash
npm run start
```

Or directly via Python:

```bash
HOST=127.0.0.1 PORT=8000 PYTHONPATH=backend backend/.venv/bin/python backend/server.py
```

### Confirm Health Check
Open `http://127.0.0.1:8000/health` in your browser. You should receive:

```json
{
  "status": "healthy",
  "version": "3.0.0"
}
```

Open `http://127.0.0.1:8000/ready` to verify that both MongoDB Atlas and Supabase Auth are connected.

---

## 6. Installing the Extension in Chrome / Brave

1. Open your browser and navigate to:
   - **Chrome**: `chrome://extensions`
   - **Brave**: `brave://extensions`
2. In the top right corner, switch on **Developer mode**.
3. Click the **Load unpacked** button.
4. Select the directory:
   `<path-to-repo>/context-passport/v3/dist`
5. The **Context Passport** extension will appear in your extensions list.
6. Click the Extensions puzzle icon in your browser toolbar and pin **Context Passport** for easy access.

---

## 7. Extension Usage & Configuration

1. **Open the Side Panel**:
   Click the Context Passport icon in the browser toolbar.
2. **Configure Settings**:
   - Go to the **Settings** tab.
   - **Backend URL**: Keep as `http://localhost:8000` (or `http://127.0.0.1:8000`).
   - **Authentication**: Sign in using your Supabase account email and password, or provide a test Bearer token.
   - **Site Capture Toggles**: Capture is **OFF by default**. Turn ON capture for the sites you wish to monitor (**ChatGPT**, **Claude**, or **Gemini Web**).
3. **Capture Memories**:
   - Visit `chatgpt.com`, `claude.ai`, or `gemini.google.com`.
   - Send regular user prompts. Completed user messages are distilled into atomic facts and explanation preferences.
   - Assistant responses, old chat history, and injected memory blocks are never captured.
4. **Use Memory**:
   - Type a prompt in the message composer.
   - Click the inline **Use Memory** button (or use the side-panel Fallback tab).
   - Up to 3 relevant general memories are automatically formatted into the prompt.
   - Any sensitive memories trigger the Privacy Firewall consent prompt ("Allow once" vs "Don't use").
5. **Manage Your Vault**:
   - In the **Vault** tab, view your distilled memories with evidence excerpts.
   - Click **Correct** to edit wording (re-embedded immediately).
   - Click **Block** to exclude from retrieval.
   - Click **Forget** to delete completely and tombstone the source against retry duplication.
6. **Sign Out**:
   - Click **Sign out** in the Settings tab. All tokens, email, and on-screen memories are immediately purged from local state.

---

## 8. Troubleshooting Guide

### 1. "Backend server offline" or Connection Refused
- Ensure the backend is running with `npm run start`.
- Confirm FastAPI is listening on `127.0.0.1:8000` (`http://localhost:8000`).
- Ensure no other service is occupying port 8000 (`lsof -i :8000`).

### 2. "Authentication expired" or HTTP 401
- Supabase Auth tokens expire periodically.
- Open the extension side panel, go to **Settings**, and sign in again with your email and password.

### 3. MongoDB Atlas Connection Timeout or ServerSelectionTimeoutError
- Check your MongoDB Atlas Network Access settings.
- Ensure your current IP address is whitelisted in MongoDB Atlas (or `0.0.0.0/0` during development).
- Check that `MONGODB_URI` contains valid credentials.

### 4. OpenRouter Rate Limit (429) or Spending Cap Exceeded
- If OpenRouter returns a 429 rate limit or 5xx server error, the backend automatically transitions to Gemini 2.5 Flash Lite fallback.
- If the spending cap ($1.00) is hit, `UsageTracker` halts further provider calls with HTTP 503. Check usage with `/ready`.

### 5. "No memories captured yet"
- Capture is **OFF by default** to preserve privacy.
- Open the side panel, go to **Settings**, and enable the toggle for the current website (`ChatGPT`, `Claude`, or `Gemini Web`).
- Only user-authored messages sent after enabling capture will be processed.

---

## 9. Automated Testing & Verification

Run the full V3 verification suites from the repository root:

```bash
# Run TypeScript typechecks, extension unit tests, and core pytest suites
npm run test:v3

# Run the complete backend test suite offline
APP_ENV=test ALLOW_TEST_AUTH=true ALLOW_OFFLINE_EMBEDDINGS=true PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/ -q
```
