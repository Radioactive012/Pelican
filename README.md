# Context Passport

Context Passport is an opt-in, self-improving, privacy-first browser memory extension for **ChatGPT**, **Claude**, and **Gemini web**.

With capture enabled, it learns useful facts and your explanation style preferences from new user-authored messages. On each supported site, a **Use Memory** button reads your current draft, automatically selects relevant general memories, prompts for sensitive candidates, and prepares the native prompt.

## Architecture

- **Extension**: Single Manifest V3 extension for `chatgpt.com`, `claude.ai`, and `gemini.google.com` with native prompt injection, compact side-panel vault, and manual fallback route.
- **Backend**: FastAPI with Mem0 OSS, Gemini 2.5 Flash, Gemini Embeddings (`models/gemini-embedding-001`, 1536 dims), MongoDB Atlas, and Supabase Auth.
- **Storage**: MongoDB Atlas holds both Mem0 vector memories and app-control collections (`observations`, `preferences`, `dedup_events`). Supabase supplies sign-in only.

## Three-Version Roadmap

1. **Version 1 (implemented)**: Working memory and learning brain (FastAPI backend, Mem0 scoped MongoDB vector store, explanation preference engine with 3 obs / 2 chats requirement, credential screening, multi-tenant isolation).
2. **Version 2 (live gate pending)**: Three-site extension and privacy firewall (ChatGPT, Claude, Gemini adapters, Use Memory button, sensitive approval flow).
3. **Version 3 (in development)**: Feedback and controls in the [V3 extension](v3/README.md), including vault corrections, lockable preferences, evidence removal, and forget/block lifecycles. The end-to-end browser journey is pending.

See [AGENTS.md](AGENTS.md) for full architecture and test criteria.

For production capture and recall, verify that the Google project owning the
Gemini API key has paid-tier billing and set `GEMINI_PAID_TIER_CONFIRMED=true`.
API-key connectivity alone cannot verify billing; without this confirmation the
backend rejects conversation ingestion and memory queries. Use synthetic data
until billing is confirmed. V1 recall uses a conservative similarity cutoff so
weak matches are not sent to another chat service.
