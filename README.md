# Context Passport

Context Passport is an opt-in, self-improving, privacy-first browser memory extension for **ChatGPT**, **Claude**, and **Gemini web**.

With capture enabled, it learns useful facts and your explanation style preferences from new user-authored messages. On each supported site, a **Use Memory** button reads your current draft, automatically selects relevant general memories, prompts for sensitive candidates, and prepares the native prompt.

## Architecture & Hosting

- **Extension**: Single Manifest V3 extension for `chatgpt.com`, `claude.ai`, and `gemini.google.com` with native prompt injection, compact side-panel vault, and manual fallback route.
- **Backend**: Local FastAPI backend running on `http://localhost:8000` bound strictly to `127.0.0.1`. No Render or public cloud backend hosting in this plan.
- **Model Routing (Provisional & Transition)**:
  - Transitioning to a provider-aware backend abstraction.
  - Provisional primary extractor: OpenRouter GLM 5.3 Flash (`openrouter/glm-5.3-flash`).
  - Candidate fallback extractor: Gemini 2.5 Flash Lite via OpenRouter (`google/gemini-2.5-flash-lite`).
  - Provisional 1536-dimensional embedder: `openai/text-embedding-3-small` via OpenRouter.
  - Optional validation pass: Feature-flagged Jev (TypeSafe AI) for selective second-pass quality/privacy verification.
  - Benchmarking is conducted before finalizing model selections. Steps 1–9 use mocks/synthetic data; real provider keys are connected only at Step 10.
- **Storage**: MongoDB Atlas holds both Mem0 vector memories and app-control collections (`observations`, `preferences`, `dedup_events`, `forgotten_preferences`, `forgotten_memory_sources`). Supabase supplies sign-in only.

## Current Roadmap & Status

1. **Version 1 (implemented baseline)**: Working memory and learning brain (FastAPI backend, Mem0 scoped MongoDB vector store, explanation preference engine with 3 obs / 2 chats requirement, credential screening, multi-tenant isolation).
2. **Version 2 (contract gates pass; live gate pending)**: Three-site extension and privacy firewall (ChatGPT, Claude, Gemini adapters, Use Memory button, sensitive approval flow). Simulated contract tests pass; real-browser verification is pending.
3. **Version 3 (in development)**: Feedback and controls in the [V3 extension](v3/README.md), including vault corrections, lockable preferences, evidence removal, and forget/block lifecycles. Automated lifecycle tests pass. Live browser judge journey (Tests 11–12) is pending.

See [AGENTS.md](AGENTS.md) and [V3_ANTIGRAVITY_EXECUTION_PLAN.md](V3_ANTIGRAVITY_EXECUTION_PLAN.md) for full architecture, testing discipline, and step-by-step gates.

