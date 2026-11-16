# Step 8 part B: acceptance criteria and test cases (AR-05, FR-10)

## Context
Step 8A ends with approved requirements, but nothing yet says how an approved requirement will be checked. This step lets the lead:
- generate acceptance criteria and 2–5 Given/When/Then test cases (positive, negative, edge) for each approved requirement, through the LLM, with each test case linked to the criterion it verifies;
- review them: accept, edit (which creates a new version), reject, or add by hand;
- see which test cases may be out of date after the requirement changed ("needs review");
- export approved requirements with their criteria and accepted test cases as CSV and PDF.

**Decisions** (from your answers and from reading the code):
- **PDF:** `reportlab` is the only new package. Its built-in fonts cover Latin text only, so a Bangla project name would show as boxes in the PDF. Requirements and test cases are English (rule 10), and the CSV keeps every character.
- **Criteria** work like test cases: origin, status, and a version row on every edit. Accepting a test case also accepts its linked criterion if that criterion is still `suggested`. Rejecting a criterion returns 409 while test cases that are not rejected still link to it.
- **"Generate for all approved"** covers approved requirements that have no test cases yet, or whose untouched AI suggestions are marked "needs review". The panel button regenerates one requirement.
- **Needs review** is set only when the requirement gets a new version: an edit, or accepting a proposal, both through `add_user_version`. "Remove approval" alone does not set it. Accepting or editing the item clears it.
- **LLM:** the required `LLM` dependency, so Groq is asked first (LLM_CHAIN `groq,gemini`). No rule-based fallback: without a model the API returns 503, and the lead can still add test cases by hand.
- The project owner is the lead (`OwnedProject`), as in 8A.

## Branch and commits
1. `git checkout -b step-8b-tests`. Save this plan as `docs/plans/step-8b-plan.md` and commit "Add Step 8B plan".
2. Make small commits, roughly:
   1. models and migration
   2. generation
   3. review API
   4. needs review
   5. export, with reportlab added
   6. backend tests for each of these
   7. frontend API and types
   8. Test Cases page
   9. requirement panel section
   10. docs
3. No Co-Authored-By lines. No push.

## Data model (migration `f0a1b2c3d4e5_test_cases`, down_revision `e9f0a1b2c3d4`)
Four new tables, models in `backend/app/models/test_case.py`, registered in `models/__init__.py`. Each table runs `ALTER TABLE … ENABLE ROW LEVEL SECURITY` under `if op.get_bind().dialect.name == "postgresql":`, the same way as [e9f0a1b2c3d4_clarifications.py](backend/alembic/versions/e9f0a1b2c3d4_clarifications.py). Downgrade drops the tables and the two counter columns.

| Table | Columns (besides id, created_at) |
|---|---|
| `acceptance_criteria` | `requirement_id` FK CASCADE; `number` (AC-1, AC-2 … within the requirement, never reused); `origin` ai\|user; `status` suggested\|accepted\|edited\|rejected (CHECK); `needs_review` bool; `requirement_version` (the version it was written for); `ai_model`; `created_by_id`; `updated_at` |
| `acceptance_criterion_versions` | `criterion_id` FK CASCADE; `version`; `text`; `origin`; `created_by_id`; unique (criterion_id, version) |
| `test_cases` | `requirement_id` FK CASCADE; `number` (TC-1 …); `origin`; `status` (same CHECK); `needs_review`; `requirement_version`; `ai_model`; `created_by_id`; `updated_at` |
| `test_case_versions` | `test_case_id` FK CASCADE; `version`; `title`; `type` positive\|negative\|edge (CHECK); `given`; `when`; `then`; `criterion_id` FK `acceptance_criteria` **SET NULL**; `origin`; `created_by_id`; unique (test_case_id, version) |

`requirements` gets `criterion_counter` and `test_case_counter` (int, server default 0), so codes are never reused after a regeneration.

`Requirement` gets the relationships `criteria` and `test_cases`. Both models get a `current` property (the last version), the same way `Requirement` does. Versions are only ever added (rule 6).

## Backend

### 1. Generation (AR-05): new `backend/app/ai/test_cases.py` and prompt `app/ai/prompts/test_cases.md`
- `generate_test_cases(requirements, llm, *, replace)` follows the shape of [clarify.py](backend/app/ai/clarify.py) `generate_questions`. It makes one LLM call per requirement, sequentially, with one overall deadline `LLM_SECONDS = 150` (below the frontend proxy's 285 s).
  - When the time runs out, the remaining requirements are reported as `skipped`, and the UI says "Run again for the rest".
  - An `LLMError` on the first call is raised, and nothing is stored. On a later call, the earlier results are kept and the error is returned in the result.
- **Eligible:** only requirements with `approval is not None`. The single-requirement endpoint returns 409 "Only approved requirements get test cases".
- **Replace (rule 2):** it first deletes `ai`+`suggested` test cases. It then deletes `ai`+`suggested` criteria that no remaining test case links to. Accepted, edited, user-written and rejected items always stay.
- **Prompt input:**
  - the current statement, type, NFR category and actor;
  - up to 3 client source sentences (these may be Bangla or Banglish; the LLM reads them);
  - the kept criteria as `{ref: "E<id>", text}`;
  - the titles of the kept test cases (so they are not repeated).
- **Prompt instructions:**
  - testable acceptance criteria and 2–5 Given/When/Then test cases covering positive, negative and edge types, in English;
  - no invented numbers or behaviour that is not in the requirement;
  - cautious wording;
  - set `not_enough_information` with a reason when the requirement is too unclear to test.
- **Schemas** go in `schemas/test_case.py`. The LLM answer is `{not_enough_information, reason, criteria:[{ref:"C1", text}], test_cases:[{title, type, given, when, then, criterion_ref}]}`. Its list limits are generous so that one extra item does not fail the whole answer; the code applies the real limits. The code:
  - drops test cases whose `criterion_ref` is unknown, whose fields are empty, or whose title is a duplicate;
  - caps the result at 5 test cases and 8 criteria;
  - keeps only new criteria that at least one test case links to;
  - stores nothing when the answer is `not_enough_information`, or when fewer than 2 usable test cases remain and the requirement has no kept ones. That case is reported as "not enough information".
  - otherwise stores the items with `origin=ai`, `status=suggested`, `ai_model=llm.last_model` and `requirement_version=current.version`.
- **Result:** `{created_test_cases, created_criteria, items:[{requirement_id, code, created, not_enough_information, reason}], skipped:[codes], error}`.

### 2. Router `backend/app/api/test_cases.py` (owner only, mounted in [main.py](backend/app/main.py))
- `POST /projects/{id}/requirements/{rid}/test-cases/generate` regenerates one requirement (`replace=True`).
- `POST /projects/{id}/test-cases/generate` runs "for all approved", over the set described in the decisions.
- `GET /projects/{id}/test-cases?status=&type=&requirement_id=&needs_review=` returns the items grouped by requirement: `[{requirement:{id, code, statement, approved, version}, criteria:[…], test_cases:[…]}]`.
  - Groups include approved requirements and any requirement that has criteria or test cases.
  - Each item carries its current fields, `version_count`, `versions` (for the history view), `needs_review`, and the criterion code (or "criterion rejected / removed").
- **Test cases:**
  - `POST …/requirements/{rid}/test-cases` adds one by hand: `origin=user`, `status=accepted`, version 1 by the lead. Only for approved requirements; the criterion must belong to the requirement.
  - `PATCH …/test-cases/{tid}` edits it. It adds a version (`origin=user`, `created_by` = the lead), sets status `edited` and clears `needs_review`. Nothing changes when the fields are the same.
  - `POST …/test-cases/{tid}/accept` sets `accepted`, clears `needs_review`, and also accepts the linked criterion if it is `suggested`.
  - `POST …/test-cases/{tid}/reject` sets `rejected`.
- **Criteria:** `POST …/requirements/{rid}/criteria`, `PATCH …/criteria/{cid}`, `POST …/criteria/{cid}/accept` and `POST …/criteria/{cid}/reject`, with the same rules. Reject returns 409 while a test case that is not rejected links to the criterion.
- **Errors:** `LLMError` is turned into 502/503 by the same `_llm_error` helper as in [clarifications.py](backend/app/api/clarifications.py) (moved to `deps.py` so both routers share it). Another owner's project or item returns 404.

### 3. Needs review, in [requirements.py](backend/app/api/requirements.py) `add_user_version`
- After the new version is appended, every criterion and test case of the requirement that is not rejected gets `needs_review = True`.
- Because accepting a proposal also goes through `add_user_version`, it is covered as well.
- `_LOAD` gets `selectinload(Requirement.criteria)` and `selectinload(Requirement.test_cases)`.
- `_out` gains:
  - `test_case_count` (not rejected);
  - `accepted_test_cases`;
  - `test_cases_need_review`.

### 4. Export (FR-10): new `backend/app/reports/test_case_export.py`
- `export_rows(project)` collects the data for both formats:
  - approved requirements in code order, each with the approved version's statement;
  - the requirement's criteria that are accepted or edited;
  - the requirement's test cases that are accepted or edited, with a "needs review" mark when it is set.
  - An approved requirement with nothing accepted yet is still listed, with "No accepted test cases yet".
- `GET /projects/{id}/test-cases/export.csv`:
  - UTF-8 with a BOM, so Excel shows any language correctly;
  - first the rows `Project,<name>` and `Exported,<date UTC>`, then a blank row, then one row per test case with these columns: requirement code, statement, version, criterion code, criterion, test case code, title, type, Given, When, Then, status, needs review. A criterion without a test case gets its own row;
  - cells that start with `= + - @` are prefixed with `'` (CSV formula injection: the text can come from the client);
  - `Content-Disposition` gives the file name `<project-slug>-test-cases-<YYYY-MM-DD>.csv`.
- `GET …/export.pdf` is built with reportlab platypus:
  - a title, the project name and the export date;
  - a cautious note: "Generated with AI assistance and reviewed by the team. It lists the planned checks; it does not show that the system is complete or ready to release.";
  - one section per requirement, with its criteria list and a test case table (code, type, Given/When/Then, criterion);
  - non-Latin characters are replaced so the build cannot fail (see the decision above).
- The Next.js proxy already forwards `content-disposition`, so the frontend can use plain download links.

## Frontend
- **Reading first:** read `node_modules/next/dist/docs/` for `useSearchParams` and Suspense in this Next version, as AGENTS.md asks.
- **[lib/api.ts](frontend/lib/api.ts):** a "Test cases" section with the types `AcceptanceCriterion`, `TestCase`, `TestCaseGroup` and `GenerateTestCasesResult`, and functions such as `generateTestCases`, `generateAllTestCases`, `listTestCases(filters)`, `addTestCase`, `editTestCase`, `acceptTestCase`, `rejectTestCase` and the criterion equivalents, plus `testCaseExportUrl(projectId, "csv" | "pdf")`. `Requirement` gains the three counts.
- **Test Cases page:**
  - `app/(dashboard)/test-cases/page.tsx` replaces the placeholder and uses a new `components/testcases/TestCasesView.tsx`.
  - Toolbar: "Generate for all approved", Export CSV and Export PDF (links), and the filters status, type and requirement. The requirement filter is pre-set from `?requirement=<id>`. There is also a "Needs review" toggle.
  - Groups are shown per requirement (code and statement, with an "Approved" or "Not approved" badge). Each group lists its criteria (AC-n) with accept, edit and reject, then its test cases (TC-n): title, type badge, Given/When/Then, linked criterion, origin and status badges, "Needs review", accept/edit/reject, and a "History" toggle that shows the versions with `lib/wordDiff`.
  - "Add test case" and "Add criterion" use a Modal. The test case form has the title, type, Given, When, Then, and a criterion select.
  - Not-enough-information results and partial errors are shown as notices, for example "Not enough information to write test cases for REQ-004: …".
- **Requirement panel ([RequirementPanel.tsx](frontend/components/requirements/RequirementPanel.tsx)):** a new "Test cases" section in `components/requirements/TestCasesSummary.tsx`:
  - the counts, e.g. "5 test cases · 3 accepted · 2 need review";
  - a link to `/test-cases?requirement=<id>`;
  - "Generate test cases" only when the requirement is approved; otherwise the hint "Approve the requirement to generate test cases".
- **EditDialog:** when the requirement has test cases, it adds the warning "Its N test cases will be marked 'needs review'".

## Tests (mocked LLM, no real APIs)
- **Fake LLM:** extend [tests/fake_llm.py](backend/tests/fake_llm.py) so it answers `test_cases`. There is a default answer of 2 criteria and 3 test cases (positive, negative, edge), per-requirement overrides through `test_case_answers`, a `test_cases_fail` setting, and `test_case_calls` to record the calls. The tests reuse [clarify_helpers.py](backend/tests/clarify_helpers.py) (`seed_requirement`, `use_llm`) plus a small `approve(...)` helper.
- **`test_test_case_generation.py`:**
  - items are stored with origin ai, status suggested, the model, the requirement version and the links to their criteria;
  - a requirement that is not approved gets 409;
  - not enough information, and fewer than 2 usable test cases, store nothing;
  - unknown criterion refs are dropped, the cap of 5 holds, and unused new criteria are dropped;
  - "for all approved" covers the right set, keeps earlier results on a later error, and reports skipped requirements when the deadline is reached (with a patched clock);
  - no LLM gives 503 and LLMError gives 502;
  - another owner's project gives 404.
- **`test_test_case_regenerate.py`:** regenerating replaces only untouched AI suggestions. Accepted, edited, user-written and rejected items stay, a criterion linked from a kept test case stays, and codes are not reused.
- **`test_test_case_review.py`:**
  - edit adds a version (origin user), sets status edited and clears needs review;
  - accept also accepts a suggested criterion;
  - reject;
  - add by hand works on an approved requirement and gives 409 on a requirement that is not approved;
  - a foreign criterion gives 404;
  - rejecting a criterion that is still in use gives 409;
  - the list filters work.
- **`test_test_case_needs_review.py`:**
  - editing an approved requirement removes the approval and marks its items "needs review";
  - accepting a proposal does the same;
  - rejected items are not marked;
  - "Remove approval" alone does not mark them;
  - accepting an item clears the mark;
  - "for all approved" picks the requirement up again after it is re-approved.
- **`test_test_case_export.py`:**
  - the CSV has the project and date rows and only approved requirements with accepted or edited items;
  - its cells escape formulas;
  - a Bangla project name survives in UTF-8;
  - the PDF answers `application/pdf`, starts with `%PDF`, and its build does not fail with a Bangla name;
  - `export_rows` is checked directly.
- **[test_migrations.py](backend/tests/test_migrations.py):** assert the new tables, the CHECK constraints and the on-delete rules, and that downgrade works.
- **[test_postgresql.py](backend/tests/test_postgresql.py):** RLS is enabled on the 4 tables, and the whole flow runs on PostgreSQL: approve, generate, edit, accept, export CSV.

## Verification
1. `cd backend && pip install reportlab` (after adding it to `requirements.txt`), then `pytest -q`: everything green.
2. PostgreSQL: Supabase is at `e9f0a1b2c3d4`. Before running `alembic upgrade head` (to `f0a1b2c3d4e5`) on Supabase I will **ask you**. Then `TEST_DATABASE_URL=<DATABASE_URL> pytest tests/test_postgresql.py`.
3. `cd frontend && npm run lint && npm run build`.
4. A manual walk-through:
   1. approve a requirement, then "Generate test cases" in its panel; the counts appear;
   2. open the Test Cases page from the link, which is pre-filtered;
   3. accept one test case, edit another (the history shows v2), reject one, and add one by hand;
   4. "Generate for all approved";
   5. edit the approved requirement; its items show "needs review", and generating again replaces only the untouched suggestions;
   6. Export CSV (open it in Excel) and Export PDF.
5. Write `docs/test-cases.md`, the same way as `docs/clarifications.md`. `.env.example` needs no new variables. Save a memory note on the follow-ups (Supabase revision, the Latin-only PDF font).
