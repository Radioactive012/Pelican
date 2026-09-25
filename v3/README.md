# Context Passport V3

V3 adds feedback and lifecycle controls to the three-site extension. Its source and build are isolated from V2 in this folder; both use the repository's FastAPI backend.

## Local Hosting & Architecture

- **Backend runtime**: FastAPI bound strictly to `127.0.0.1:8000` (`http://localhost:8000`). Localhost is available by default in the extension manifest.
- **Model routing transition**: Provisional primary extractor is OpenRouter GLM 5.3 Flash, fallback is Gemini 2.5 Flash Lite via OpenRouter, provisional 1536d embedder is `openai/text-embedding-3-small`, with feature-flagged Jev validation pass. Steps 1–9 run against mocks; Step 10 connects real keys after benchmarking.
- **Storage**: MongoDB Atlas stores both Mem0 vector memories and app-control collections (`observations`, `preferences`, `dedup_events`, `forgotten_preferences`, `forgotten_memory_sources`). Supabase supplies sign-in only.

## What is implemented

- The vault shows each memory's wording, classification, status, source, date, and a short redacted excerpt of the distilled memory. It offers Correct, Block, and Forget controls.
- The Learned tab shows each preference's wording, evidence count, chat count, lock state, and redacted observation excerpts. It offers Correct, Block, Forget, and per-observation Remove controls.
- Correcting a preference locks the user's wording. Later inference updates counts without changing that wording. Removing evidence re-evaluates an inferred preference; insufficient evidence removes it from recall. Explicitly corrected preferences remain active.
- Blocking excludes a memory or preference from subsequent recall. Forgetting a memory deletes its Atlas record and Mem0 plaintext history, and tombstones the source event so an in-flight retry cannot recreate it. Forgetting a preference erases its evidence and suppresses older queued evidence events.
- Hosted backend URLs request access to their exact origin when saved in Settings. Localhost remains available by default.

## Build and automated checks

From the repository root:

```sh
npm run build:v3
npm run test:v3
```

To load the extension, open `brave://extensions` or `chrome://extensions`, enable Developer mode, and select `v3/dist` with **Load unpacked**. Set the backend URL and sign in from Settings. Capture remains off until enabled per site.

## Acceptance status & Gates

The automated unit and contract tests cover control lifecycles, tenant scoping, secret screening, and the SPA first-message regression.
- **V2 Contract Gate**: Passed all six simulated test cases; real live-browser verification is pending.
- **V3 Lifecycles**: Vault and preference lifecycle controls implemented in code and tested via mock suites.
- **Live Gates Pending**: Live browser verification on ChatGPT, Claude, and Gemini web (Step 11) and the end-to-end judge demonstration rehearsal (Step 12) remain pending.

