# Context Passport V3

V3 adds feedback and lifecycle controls to the three-site extension. Its source and build are isolated from V2 in this folder; both use the repository's FastAPI backend.

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

## Acceptance status

The automated tests cover control lifecycles, tenant scoping, secret screening, and the SPA first-message regression. The V2 backend contract gate now passes all six simulated cases, including cross-site recall after the recent-write catch-up fix. V3 still needs a live browser judge journey and deployed-backend verification before its roadmap gate can be marked complete.
