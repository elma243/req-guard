# Step 8 part A: clarification questions, client answers, approval (AR-04, FR-09, FR-22)

## Context
Validation (Step 7) finds vague, missing-info, conflict and change-request issues, but nothing turns them into questions for the client yet. This step lets the lead:
- generate polite questions with the AI, linked to the findings they resolve, and edit them;
- share selected questions through a no-login link that expires;
- collect the client's answers (English, Bangla or Banglish);
- turn the answers into a new requirement version;
- approve requirements under a clear High-finding rule.

Test cases are part B and are not in this step.

**Decisions so far** (from your answers and from reading the code):
- **Team lead:** the project owner is the lead. Routes stay owner-only (`OwnedProject`).
- **Links are single use:** after one submission the link is closed. Posting again or reopening it returns 410 "already answered".
- **Who can approve:** only requirements with status `accepted` or `edited`.
- **LLM:** the required `LLM` dependency from [deps.py](backend/app/api/deps.py), which calls `get_llm()` (LLM_CHAIN default `groq,gemini`, so Groq is asked first). There is no rule-based fallback for questions; without a model the API returns a clear 503 and the lead can still add questions by hand.
- **No new packages.** Tokens use the standard library (`secrets`, `hashlib`).
- **Nothing is ever sent to the client automatically.** The lead copies the link.

## Branch and commits
1. `git checkout -b step-8a-clarify`. Copy this plan to `docs/plans/step-8a-plan.md` and commit "Add Step 8A plan".
2. Make small commits, roughly:
   1. models and migration
   2. question generation
   3. links and public page API
   4. answers and proposals
   5. approval
   6. backend tests for each of these
   7. frontend API and types
   8. requirement panel
   9. Clarifications page
   10. public client page
   11. docs and .env.example
3. No Co-Authored-By lines. No push.

## Data model (migration `e9f0a1b2c3d4_clarifications`, down_revision `d8e9f0a1b2c3`)
Six new tables. Each runs `ALTER TABLE … ENABLE ROW LEVEL SECURITY` under `if op.get_bind().dialect.name == "postgresql":`, as in [d8e9f0a1b2c3_voice_notes.py](backend/alembic/versions/d8e9f0a1b2c3_voice_notes.py). Downgrade drops them. New models go in `backend/app/models/clarification.py` and are registered in `models/__init__.py`.

| Table | Columns (besides id, created_at) |
|---|---|
| `clarification_questions` | `requirement_id` FK CASCADE; `finding_id` FK `validation_findings` **SET NULL** (null means the finding was resolved by a re-validation, or the question was added by hand); `text`; `origin` ai\|user; `status` suggested\|edited\|accepted\|rejected (CHECK); `ai_model`; `created_by_id`; `updated_at` |
| `clarification_links` | `project_id` FK CASCADE; `token_hash` String(64) unique; `expires_at`; `revoked_at`; `submitted_at` (single use); `created_by_id` |
| `clarification_link_questions` | `link_id` FK CASCADE, `question_id` FK CASCADE; composite primary key |
| `clarification_answers` | `question_id` FK CASCADE; `requirement_id` FK CASCADE (stored as you asked); `link_id` FK SET NULL; `text` (exactly as received, rule 10); `answered_at`; `seen_at` (for the "New answers" badge) |
| `clarification_proposals` | `requirement_id` FK CASCADE; `based_on_version`; `answer_ids` JSON; `statement` (null when not enough information); `reason`; `not_enough_information` bool; `ai_model`; `origin`="ai"; `status` suggested\|accepted\|edited\|rejected; `decided_by_id`; `decided_at`; `version_id` FK `requirement_versions` SET NULL (the version that accepting created) |
| `requirement_approvals` | `requirement_id` FK CASCADE; `version`; `approved_by_id`; `approved_at`; `override_reason` (null when there were no open High findings); `open_high_finding_ids` JSON (a snapshot at approval time); `revoked_at`; `revoked_reason` |

`Requirement` gets relationships to `questions`, `answers` and `approvals`, plus these properties:
- `approval`: the last approval that is not revoked and whose `version == current.version`. So any new version voids the approval automatically, and the helper also sets `revoked_at` with the reason "new version".
- `new_answers`: the count of answers with `seen_at` null.
- `open_high_findings`: findings with severity `high` and status other than `rejected`, the same meaning of "open" as `open_findings`.

## Backend

### 1. Question generation (AR-04): new `backend/app/ai/clarify.py`
- `generate_questions(db, project, requirements, llm)`:
  - **Eligible requirements:** not rejected or merged, and not approved.
  - **Eligible findings:** open findings in the categories `vague`, `missing_info`, `conflict` and `change_request` that have no question linked yet.
  - **Batching:** up to 8 requirements per call to the prompt `app/ai/prompts/clarification_questions.md`. The prompt gets the statement, the client's source sentence, each finding (id, category, reason, evidence, and the related statement for pair findings), and the instructions:
    - 1 to 3 questions, in English;
    - short and polite, with no internal words such as finding, NLI or severity;
    - cautious wording;
    - set `not_enough_information` when no good question can be written.
- **Schemas** in `schemas/clarification.py`. The LLM answer is `{"requirements":[{"requirement_id", "not_enough_information", "questions":[{"finding_id","text"}]}]}`. The code then:
  - drops unknown or foreign `finding_id`s and empty or duplicate texts;
  - caps at 3 questions per requirement and at 300 characters per question;
  - stores the questions with `origin=ai`, `status=suggested` and `ai_model=llm.last_model`.
- **Regenerate (rule 2):** removes only questions that are still `ai`+`suggested`, in no link and without answers. Edited, user-written, shared and answered questions stay.
- **Result:** `{created, items:[{requirement_id, code, created, not_enough_information}]}`. The UI shows "Not enough information to write a good question for REQ-005". `LLMError` becomes a 502 with the client's message, as in the existing endpoints.

### 2. Router `backend/app/api/clarifications.py`
Owner only, mounted in [main.py](backend/app/main.py).
- `POST /projects/{id}/clarifications/generate` (all eligible requirements), and `POST /projects/{id}/requirements/{rid}/questions/generate`
- `GET /projects/{id}/clarifications/questions?requirement_id=` returns questions with a derived `state`: `draft`, `shared` (in a live link) or `answered`.
- `POST …/requirements/{rid}/questions` adds a question by hand (`origin=user`, `status=accepted`).
- `PATCH …/questions/{qid}` edits the text. An AI question becomes `edited`.
- `DELETE …/questions/{qid}` deletes a question that has no answers and is in no live link; otherwise it returns 409.
- **Links (FR-22):**
  - `POST /projects/{id}/clarifications/links {question_ids}`:
    - all the questions must belong to this project and must not be answered (404 or 409 otherwise); 1 to 20 questions;
    - the token is `secrets.token_urlsafe(32)` and only its SHA-256 is stored;
    - `expires_at` is now + 7 days (constant `LINK_DAYS = 7`);
    - selected `suggested` questions become `accepted`;
    - the response holds `url = {FRONTEND_URL}/clarify/{token}` **once only**.
  - `GET …/links` returns the state of each link: active, expired, revoked or answered.
  - `POST …/links/{lid}/revoke`.
- **Public endpoints (no login)**, on a separate `public_router` with no `CurrentUser`:
  - `GET /public/clarify/{token}` returns only `{project_name, expires_at, questions:[{id, text}]}`. It never returns requirement codes, findings or other questions.
  - `POST /public/clarify/{token}/answers {answers:[{question_id, text}]}`:
    - text up to 2000 characters, stored unchanged; at least one non-blank answer; a question outside the link gets 400;
    - writes the answers and `submitted_at` in one transaction.
  - **Errors:** unknown token 404; expired, revoked or already submitted 410, each with its own plain message.
  - **Rate limit:** per IP through the existing `enforce` / `client_ip` in [ratelimit.py](backend/app/core/ratelimit.py), at 30 per minute.
- **Answers (FR-09):**
  - `POST …/requirements/{rid}/answers/seen` marks the requirement's answers as seen and clears the badge.
  - `POST …/requirements/{rid}/proposals` calls the prompt `clarification_proposal.md` with the current statement, the open findings and the Q&A pairs not used yet. It returns a statement and reason, or `not_enough_information`. The proposal is stored as `suggested`.
  - `POST …/proposals/{pid}/accept {statement?}`:
    - returns 409 when `based_on_version` is not the current version ("The requirement changed; propose again");
    - with a changed statement the status becomes `edited`, otherwise `accepted`;
    - creates a new version (`origin=user`, `created_by` = the lead), re-runs validation and records `version_id`.
  - `POST …/proposals/{pid}/reject`.

### 3. Reuse in [requirements.py](backend/app/api/requirements.py)
- Move the version-adding body of `edit_requirement` into a helper, `add_user_version(db, project, requirement, user, changes, llm)`. It appends the version, sets status `edited`, clears `previous_status`, revokes the approval, and runs `validate_requirement` + flush + `check_similarity`. Both edit and accept-proposal use it.
- `_out` gains `approval`, `new_answers`, `open_high_findings` and `open_questions`. `_detail` gains `questions`, `answers` (the Q&A thread) and `proposals`.

### 4. Approval
- `POST …/requirements/{rid}/approve {override_reason?}` returns 409 when:
  - the status is not accepted or edited;
  - the requirement is already approved for this version;
  - there are open High findings and no non-blank reason. This message lists them with cautious wording, e.g. "2 open High findings (possible conflict with REQ-003, …). Give a reason to approve anyway."
- When it succeeds it stores the row with a snapshot of the open High finding ids.
- `POST …/unapprove`.
- `_ensure_changeable`: reject and merge of an approved requirement return 409 "Remove the approval first". Bulk reports these as skipped. Editing is still allowed; it creates a new version and the approval ends, and the UI warns before it happens.

## Frontend
- **Reading first:** read `node_modules/next/dist/docs/` as AGENTS.md asks.
- **[lib/api.ts](frontend/lib/api.ts):** a "Clarifications" section with types such as `ClarificationQuestion`, `ClarificationLink`, `ClarificationAnswer`, `Proposal` and `Approval`, and functions such as `generateQuestions`, `createLink`, `revokeLink`, `proposeUpdate`, `acceptProposal`, `approveRequirement`, and so on. A non-redirecting `publicGet`/`publicPost` for `/public/clarify/…`, generalised from the existing `publicPost`.
- **Requirement panel ([RequirementPanel.tsx](frontend/components/requirements/RequirementPanel.tsx)), new `components/requirements/Clarifications.tsx`:**
  - questions with the finding each one resolves (or "resolved"), with Generate, Edit, Delete and Add;
  - a Q&A thread showing the time, a "New" marker, and Mark seen when the thread opens;
  - Propose update shows a diff against the current statement (reusing `lib/wordDiff`), with Accept, Edit & accept and Reject;
  - an Approved badge and an Approve button in `PanelActions` (RequirementsView.tsx about line 682). When there are open High findings, a Modal asks for an override reason.
- **Requirement table:** a "New answers" badge and an "Approved" badge (`labels.tsx`).
- **New page `app/(dashboard)/clarifications/page.tsx` (Sidebar item "Clarifications"):**
  - every question grouped by requirement, with checkboxes and Generate for all;
  - Create link opens a Modal that shows the URL once with Copy and the note "shown only once; nothing is sent to the client";
  - a links table with its state and Revoke;
  - a sidebar badge for new answers, using the `newMessages.ts` pattern (`GET …/clarifications/unseen`).
- **Public page `app/clarify/[token]/page.tsx`** (outside the dashboard group, no sidebar):
  - add `/clarify/` to `OPEN_PAGES` as a prefix in [proxy.ts](frontend/proxy.ts), with `referrer: no-referrer` as on reset-password;
  - it is mobile-first: one column, large textareas, `lang`-neutral, with the hint "You can answer in English, বাংলা or Banglish";
  - its states are loading, invalid, expired, revoked, already answered, form and thanks;
  - it calls the backend through the existing `/api/backend` proxy, which passes the client IP for the rate limit.

## Tests (mocked LLM, no real APIs)
- **Fake LLM:** extend [tests/fake_llm.py](backend/tests/fake_llm.py) so `FakeLLM` answers `clarification_questions` and `clarification_proposal` from settable dicts, with a `fail` flag. Tests override `get_llm_client`.
- **`test_clarification_questions.py`:**
  - only the four categories are used, and the finding_id is linked;
  - unknown ids are dropped and the cap of 3 holds;
  - not enough information;
  - regenerate keeps edited, user, shared and answered questions;
  - edit, delete and add, and delete is refused when shared;
  - no LLM gives 503 and LLMError gives 502;
  - another owner's project gives 404.
- **`test_clarification_links.py`:**
  - the token is stored hashed (no plaintext in the DB);
  - the public GET works without login and shows only the selected questions;
  - an unknown token gives 404; expired (`expires_at` set in the past), revoked and reused (second POST, then GET) give 410;
  - a question outside the link gives 400; blank answers and over-long answers give 422;
  - Bangla and Banglish answers are stored byte-identical;
  - the rate limit gives 429;
  - another project's questions cannot be linked.
- **`test_clarification_answers.py`:**
  - each answer is linked to its question, requirement and time;
  - `new_answers` and mark seen;
  - propose works and not enough information is handled;
  - accepting creates a new version with `origin=user`, status `edited`, and a re-validation that removes the vague finding;
  - edit & accept;
  - a stale proposal gives 409;
  - reject.
- **`test_approval.py`:**
  - approving works when there are no High findings;
  - High findings give 409, and override records the reason and the snapshot;
  - a rejected High finding no longer blocks;
  - suggested, rejected and merged requirements give 409;
  - unapprove works, and a new version voids the approval;
  - reject and merge give 409 while approved, and bulk reports them as skipped.
- **[test_migrations.py](backend/tests/test_migrations.py):** assert the new tables and constraints, and that downgrade works.
- **[test_postgresql.py](backend/tests/test_postgresql.py):** RLS is enabled on all six tables, and the whole flow (generate, link, public answer, proposal accept, approve with override) runs on PostgreSQL.

## Verification
1. `cd backend && pytest -q`: everything green.
2. PostgreSQL: Supabase is at `d8e9f0a1b2c3`. Before running `alembic upgrade head` (to `e9f0a1b2c3d4`) on Supabase I will **ask you**. Then `TEST_DATABASE_URL=<DATABASE_URL> pytest tests/test_postgresql.py`.
3. `cd frontend && npm run lint && npm run build`.
4. A manual walk-through:
   1. a vague requirement (e.g. "fast"), then Generate questions;
   2. edit a question, then create the link and copy it;
   3. open it in a private window at phone width and answer in Banglish;
   4. the "New answers" badge appears;
   5. Propose update, then Accept, which gives a new version with the vague finding gone;
   6. Approve, then try approving another requirement that has a High finding, which asks for a reason;
   7. reopening the link shows "already answered"; Revoke works on another link.
5. Update `.env.example` (no new variables are expected; FRONTEND_URL is already used for the link) and the docs. Save a memory note on the follow-ups.
