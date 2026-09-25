# Context Passport

Context Passport is a portable-memory proof of concept for AI assistants. This
repository deliberately stops at the integration gate: the dashboard and
browser extension are empty workspaces until real Gemini -> Mem0 -> MongoDB
memory, tenant isolation, deployment, and a Cursor MCP call all pass.

## Repository layout

```text
context-passport/
├── backend/                  Python 3.11 service and integration proofs
│   ├── memory_manager.py     Tenant-safe Mem0 facade
│   ├── scoped_mongodb.py     Atlas pre-filtered vector adapter
│   ├── server.py             Health routes and remote MCP ping
│   ├── test_isolation.py     Real two-user Gemini/MongoDB proof
│   ├── verify_atlas_index.py Atlas readiness proof
│   ├── verify_mcp.py         Real Streamable HTTP MCP client call
│   └── tests/                Offline regression tests
├── dashboard/                Empty frontend workspace (post-gate)
├── extension/                Empty extension workspace (post-gate)
├── render.yaml               Render Blueprint
└── .cursor/mcp.json.example  Cursor remote MCP configuration template
```

The installed Mem0 behavior and the reason for the MongoDB adapter are recorded
in [MEM0_AUDIT.md](MEM0_AUDIT.md).

## Exact compatibility set

- Python `3.11.15`
- Mem0 OSS `2.2.0`
- Google Gen AI SDK `2.25.0`
- Gemini extraction model `gemini-2.5-flash`
- Gemini embedding model `models/gemini-embedding-001`, output `1536` dimensions
- PyMongo `4.18.2`
- MCP Python SDK `2.2.0`
- FastAPI `0.141.1`, Starlette `1.7.0`, Uvicorn `0.37.0`

Every transitive Python package is pinned in `backend/requirements.lock`. The
lock has been installed from scratch and its test suite passes.

## Setup

```bash
cp .env.example .env
# Fill the development service credentials in .env; never commit it.

python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
npm install
```

Required external development resources:

- a Gemini API key;
- a MongoDB Atlas cluster whose database user can create Search indexes;
- a Supabase project and development keys;
- a Render service created from `render.yaml`.

## Gate sequence

Run these in order from the repository root:

```bash
./scripts/clean_install.sh
npm run gate:audit
npm run gate:index
npm run gate:memory
npm start
```

In another terminal, prove the local MCP protocol call:

```bash
npm run gate:mcp
```

After deploying the Blueprint, set `RENDER_EXTERNAL_URL` in `.env` and run:

```bash
backend/.venv/bin/python backend/check_services.py
npm run gate:mcp
```

Copy `.cursor/mcp.json.example` to `.cursor/mcp.json`, replace the URL with the
Render service, reload Cursor, and call `context-passport`'s `ping` tool. The
gate is complete only when Cursor visibly returns the hosted build marker.

## Gate status

| Check | Status |
|---|---|
| Offline unit/regression tests | Passed: 7 tests |
| Full clean reinstall from lock | Passed |
| Installed Mem0 behavior audit | Passed; unsafe assumptions compensated |
| Local `/health` | Passed |
| Local MCP initialize/list/call `ping` | Passed |
| Real Gemini extraction and embedding | Waiting for `.env` credentials |
| MongoDB storage, ready Atlas index, two-user isolation | Waiting for `.env` credentials |
| Supabase development project check | Waiting for `.env` credentials |
| Hosted Render health and MCP call | Waiting for deployment |
| Actual Cursor call to hosted `ping` | Waiting for hosted URL and Cursor |

Do not start dashboard or extension screen development until every row is
passed.
