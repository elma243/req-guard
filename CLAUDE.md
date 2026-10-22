# ReqGuard — project context for Claude Code

## What we are building
ReqGuard is a web platform with two parts:

- **Part A — requirements from client communication.** Import client messages (Jira, Slack, Telegram, Discord, Gmail by API; WhatsApp and Messenger by chat-export upload; .eml and meeting notes by file upload) into one message format. Use NLP + an LLM to extract requirements (with the source sentence), classify them (functional / non-functional), validate them (vague, missing information, duplicate, conflict, out of scope, module alignment), generate clarification questions, let the team lead approve, generate acceptance criteria and Given/When/Then test cases, and group requirements by module with a suggested assignee.
- **Part B — traceability and release.** Connect a GitHub Python repository, parse code and tests, recover requirement → code → test links (AI suggests, user confirms), detect two-way drift between two commits, check whether each acceptance criterion has a verifying test, apply rule-based risk findings, and produce a release readiness report (dashboard + PDF + optional pull-request comment).

Capstone project, 2 students, 6 weeks. Keep it simple, working and demo-able. Do not add features that are not in BUILD_PLAN.md.

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, pytest
- Database: PostgreSQL (Supabase) with pgvector; SQLite allowed only for local unit tests
- NLP: spaCy (en_core_web_sm), sentence-transformers (all-MiniLM-L6-v2), Hugging Face NLI model (cross-encoder/nli-deberta-v3-small), scikit-learn
- LLM: provider-agnostic client in `backend/app/ai/llm.py`; model and key from env vars; always request JSON and validate with a Pydantic schema
- Code analysis: GitPython, Python `ast`
- Frontend: Next.js (App Router) + TypeScript + Tailwind CSS, React Flow for graphs
- Deployment: Vercel (frontend), Render or Railway (backend)

## Repository layout
```
backend/
  app/
    main.py            # FastAPI app, routers
    core/              # config (env), security, db session
    models/            # SQLAlchemy models
    schemas/           # Pydantic schemas
    api/               # routers: auth, projects, team, sources, messages, requirements, tests, tasks, repos, trace, analysis, reports
    connectors/        # jira.py, slack.py, telegram.py, discord.py, gmail.py, whatsapp_export.py, messenger_export.py, files.py, base.py
    nlp/               # extraction.py, classify.py, validate.py, similarity.py, nli.py, cluster.py
    ai/                # llm.py, prompts/*.md, generators (questions, test cases, explanations)
    code/              # repo fetch, parser, import graph, diff
    trace/             # embeddings index, link recovery, criterion test check
    rules/             # drift rules, risk rules
    reports/           # report builder, PDF export, PR comment
  tests/
  alembic/
frontend/
  app/ (pages)  components/  lib/api.ts
eval/
  data/  scripts/  results/
docs/
```

## Rules (always follow)
1. **Every message keeps its source.** Normalised message = `source, external_id, channel, author, sent_at, text, raw`. Every requirement stores the message id(s) and the exact source sentence it came from.
2. **AI suggests, humans decide.** Every AI output has `origin = "ai"` and a status (`suggested`, `accepted`, `edited`, `rejected`). Never overwrite an item whose origin is `user` or whose status is `accepted`/`rejected`.
3. **LLM calls**: go through `app/ai/llm.py` only; prompts live in `app/ai/prompts/*.md`; output must be JSON validated by a Pydantic model; on invalid JSON retry once, then return a clear error. If evidence is insufficient the model must answer "not enough information" instead of guessing.
4. **Deterministic first**: diffs, imports, dependency changes and drift rules are plain code, not AI.
5. **Secrets** only in `.env` (never committed). Tokens are never sent to the frontend. Keep `.env.example` up to date.
6. **Requirement versions**: editing a requirement creates a new version row; never update text in place.
7. **Cautious wording** in findings: "possible conflict", "potential test gap". Never say a release is safe.
8. Write pytest tests for: connectors' parsers (with fixture files), validation rules, drift rules, risk rules. Do not call real external APIs in tests — use fixtures/mocks.
9. Small commits with clear messages. After finishing a task, run `pytest` (backend) and `npm run lint && npm run build` (frontend) before saying it is done.
10. 10. Client messages may be in English, Bangla (Bengali script) or Banglish (Bangla written in Latin letters), often mixed in one message. Store message text exactly as received. Extracted requirements are written in English, and each keeps the original source sentence unchanged. Analysed repositories: Python only.

## Commands
- Backend: `cd backend && uvicorn app.main:app --reload` · tests: `cd backend && pytest -q` · migrations: `alembic revision --autogenerate -m "..." && alembic upgrade head`
- Frontend: `cd frontend && npm run dev`
- Eval: `cd eval && python scripts/<name>.py`