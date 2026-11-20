# Step 10: GitHub repository analysis and requirement → code → test trace links

## Context
Part B starts here. After Step 9 the lead has approved requirements, each with acceptance criteria and Given/When/Then test cases (Step 8B), but nothing yet ties them to the code. This step:
- links one GitHub Python repo to a project;
- lets the lead analyse it;
- recovers trace links from approved requirements to code (files, commits, PRs) and tests (test files and functions);
- shows a coverage level per requirement and for the project.

AI only suggests links; people confirm or reject them (rule 2). Drift detection is Step 11 and is not started here.

**Decisions** (your answers plus what the code shows):
- **Code source: REST tarball.**
  - One `GET /repos/{o}/{r}/tarball/{sha}` downloads the repo at the head commit.
  - It is parsed with `ast` + `tokenize` (stdlib), so there is no new package and no git binary.
  - It is downloaded again only when the head SHA changes.
  - Commits, PRs and branches come from the REST API, cached by SHA / PR number. This differs from BUILD_PLAN's GitPython clone, which needs git on Render. Step 11 can reuse the parsed functions and the compare API.
- **Coverage levels:** Not started / Code only / **Tests only** (5th level, added) / Code + tests / **Verified**.
  - Verified = Code + tests, and every accepted acceptance criterion has a linked test function. The link goes through a matched Step 8B test case.
  - A requirement with no accepted criteria cannot be Verified; it says why.
- **Which links count toward coverage:**
  - an explicit (REQ-ID) link counts unless it is rejected;
  - an AI suggestion counts only after a person confirms it;
  - a manual link counts at once.
- **Links target stable keys, not snapshot rows.** Examples: `file:app/auth.py`, `test_function:tests/test_auth.py::test_reset_expires`, `commit:<sha>`, `pr:12`. Re-analysis never loses a decision, and a rejected key is never suggested again. The table is unique on (requirement, target_type, target_key).
- `GITHUB_TOKEN` (already in config and `.env.example`) is optional for public repos (60 requests/hour without it). It is only sent to api.github.com. Responses carry only `token_configured: bool`.
- The project owner is the lead (`OwnedProject`), as in Steps 8 and 9.

## Branch and commits
We are already on `step-10-traceability`. First, save this plan as `docs/plans/step-10-plan.md` and commit "Add Step 10 plan". Then make small commits, roughly:
1. models + migration
2. GitHub client
3. parser
4. explicit links
5. suggestions + prompt
6. coverage
7. API
8. CSV
9. backend tests (with each part)
10. frontend API/types
11. repo card on the project and Repository pages
12. Traceability page
13. requirement panel section
14. docs

No Co-Authored-By lines (rule 11). No push.

## Data model: `backend/app/models/repository.py` + `trace.py`, migration `b2c3d4e5f6a7_repositories_and_trace_links` (down_revision `a1b2c3d4e5f6`)
`TABLES` lists all 7 tables. Each one gets `ALTER TABLE … ENABLE ROW LEVEL SECURITY` under `if op.get_bind().dialect.name == "postgresql":`, the same pattern as [a1b2c3d4e5f6_tasks.py](backend/alembic/versions/a1b2c3d4e5f6_tasks.py). Downgrade drops them all.

| Table | Columns (besides id, timestamps) |
|---|---|
| `repositories` | `project_id` FK CASCADE **unique** (one repo per project); `owner`; `name`; `default_branch`; `private` bool; `head_sha`; `last_analyzed_at`; `last_result` JSON (counts + notices of the last run); `pulls_synced_at` (PR cursor) |
| `repo_snapshots` | `repository_id` FK CASCADE; `commit_sha`; `file_count`, `python_files`, `test_functions`; `truncated` bool; unique (repository_id, commit_sha) |
| `repo_files` | `snapshot_id` FK CASCADE; `path`; `kind` code\|test\|other (CHECK); `symbols` JSON `[{kind function\|class\|test, name, qualname, line_start, line_end, docstring}]`; `mentions` JSON `[{number, line, where comment\|docstring\|name, symbol}]`; `parse_error` |
| `repo_commits` | `repository_id` FK CASCADE; `sha`; `message`; `author`; `committed_at`; `files` JSON (changed paths); `files_fetched` bool (false when the rate limit stopped it, so the next run fills it in); unique (repository_id, sha) |
| `repo_pulls` | `repository_id` FK CASCADE; `number`; `title`; `body`; `state`; `merged` bool; `merged_at`; `head_ref`; `author`; `updated_at_github`; unique (repository_id, number) |
| `trace_target_embeddings` | `repository_id` FK CASCADE; `text_hash` (sha256 of the text); `model`; `embedding` (`EmbeddingVector`, as in [embedding.py](backend/app/models/embedding.py)); unique (repository_id, text_hash, model) |
| `trace_links` | `project_id` FK CASCADE; `repository_id` FK CASCADE; `requirement_id` FK CASCADE; `link_type` implements\|verifies (CHECK); `target_type` file\|commit\|pr\|test_file\|test_function (CHECK); `target_key`; `target_label`; `target_detail` (docstring first line / commit title, used for test case matching); `target_line`; `origin` explicit\|ai\|user (CHECK); `status` suggested\|accepted\|rejected\|dismissed (CHECK; dismissed = the AI said "no link" or "not enough information", hidden); `confidence`; `similarity`; `reason`; `evidence` (e.g. "REQ-004 in commit message"); `ai_model`; `llm_checked` bool; `requirement_version`; `decided_by_id` FK users SET NULL; `decided_at`; unique (requirement_id, target_type, target_key) |

## Backend

### 1. Settings and env: [config.py](backend/app/core/config.py) + `.env.example`
- `trace_similarity: float = 0.6`
- `github_max_commits: int = 200`: commits read per analysis, newest first
- `github_max_repo_mb: int = 50`: tarball cap

`.env.example` documents them next to `GITHUB_TOKEN` (repo read only).

### 2. GitHub client: `backend/app/code/github.py`
- `GitHubClient(token, client)` uses `send()` from [http.py](backend/app/connectors/http.py) with `allow_status=(403,)`.
  - Headers: `Accept: application/vnd.github+json`, `X-GitHub-Api-Version`, and `Bearer` only when a token is set.
  - It tracks `X-RateLimit-Remaining` / `-Reset`.
  - A 403/429 with remaining 0 raises `ConnectorError("GitHub rate limit reached; it resets at 14:05 UTC. Set GITHUB_TOKEN for 5000 requests/hour.")`.
  - A 404 on the repo says "Not found, or GITHUB_TOKEN cannot read it".
- `get_repo(owner, name)` returns the default branch, private flag and head SHA (via `GET /branches/{b}`).
- `list_new_commits(branch, known_shas, max)` pages `/commits?sha=` and stops at the first known SHA or at `max`.
- `commit_files(sha)` calls `/commits/{sha}`, but only for new commits, and only while `remaining > 50`. Otherwise it leaves `files_fetched=False` and adds a notice.
- `list_pulls_since(cursor)` pages `/pulls?state=all&sort=updated&direction=desc` and stops at `updated_at <= cursor`. `merged_at` comes from the list, so there is no request per PR.
- `list_branches()` returns the branch names and their head SHAs.
- `download_tarball(sha, max_bytes)` streams with redirects. It reuses `download()` from [http.py](backend/app/connectors/http.py), generalised with a `what` label so the size error does not say "audio file". httpx drops Authorization on the codeload redirect.

### 3. Parser: `backend/app/code/parser.py` (pure, no DB)
- `parse_tarball(data) -> list[ParsedFile]` strips the top folder. It skips `.git`, `venv`, `.venv`, `env`, `node_modules`, `site-packages`, `__pycache__`, `build` and `dist`, and `.py` files over 500 KB. Other files keep only their path (the file tree).
- **Test file:** `test_*.py`, `*_test.py`, or under a `tests/` folder.
- **Test function:** `test*` at module level or in a `Test*` class (pytest naming).
- Functions and classes are read with line ranges and docstrings (`ast`). Comments are read with `tokenize`. A file with a SyntaxError keeps `parse_error`, and its comments are still scanned line by line.
- `find_req_numbers(text)` in `backend/app/trace/explicit.py` uses `(?<![A-Za-z0-9])REQ[-_]?0*(\d{1,6})(?!\d)` (case-insensitive). It matches `REQ-004`, `req_4`, `REQ4` and `test_req_004_login`, but not `PREREQ-4`. `parse_tarball` and the explicit links both use it.

### 4. Analysis: `backend/app/code/analyze.py`, `analyze(db, project, repo, llm, gh) -> AnalysisResult`
1. Get the head SHA. If it equals `repo.head_sha` and a snapshot exists, skip the tarball (cache).
2. Fetch the new commits (plus their files), the PRs since the cursor, and the branches. Upsert them.
3. When the head changed: download the tarball, parse it, and store a `repo_snapshots` row with its `repo_files`.
4. Run `recover_links` (5 and 6 below).
5. Store `last_result`: new commits, PRs, files, tests, explicit links added, suggestions added, dismissed, unchecked, rate limit remaining, notices.

### 5. Explicit links: `backend/app/trace/explicit.py` (deterministic, rule 4)
Only **approved** requirements (`approval is not None`); unknown or unapproved numbers are ignored.

| Where the ID is found | Link |
|---|---|
| commit message | implements `commit:<sha>` |
| PR title, body or head branch | implements `pr:<n>` |
| branch name with no PR | implements `commit:<branch head sha>` |
| comment or docstring in a code file | implements `file:<path>` (line) |
| test function name, docstring or comment inside it | verifies `test_function:<path>::<qualname>` |
| anywhere else in a test file | verifies `test_file:<path>` |

Each link gets origin `explicit`, confidence 1.0, the evidence text, and status `suggested`; it counts toward coverage unless rejected. Existing keys are never touched.

### 6. Suggested links: `backend/app/trace/suggest.py` + prompt `app/ai/prompts/verify_links.md`
- **Target texts:**
  - code file: path words + class/function names split into words + the first line of the module docstring;
  - test function: name words + docstring + path words;
  - commit: message (500 chars);
  - PR: title + body (500 chars).
- **Embeddings:**
  - requirement statements use `ensure_embeddings` from [similarity.py](backend/app/nlp/similarity.py) (cached per version);
  - targets are cached in `trace_target_embeddings` by text hash.
  - If `ModelUnavailable` is raised, only explicit links are made, with a notice.
- **Candidates:** per requirement and target type, the top 3 with similarity ≥ `TRACE_SIMILARITY`. They skip any key that already has a row, except:
  - `dismissed` rows from an older requirement version (asked again);
  - suggestions with `llm_checked=False` (checked again).
  - A row with a person's decision (`rejected`, `accepted`, or origin `user`) is never changed.
- **LLM:** through `llm.complete_json("verify_links", …)` only, with up to 15 pairs per call and a deadline.
  - It sends `{pairs:[{pair_id, requirement (statement + accepted criteria), target (type, label, text)}]}` and gets back `{links:[{pair_id, verdict link|no_link|not_enough_information, confidence, reason}]}`, validated by Pydantic.
  - The prompt says to answer `not_enough_information` rather than guess, and to give a one-sentence reason.
  - `link`: status `suggested`, origin `ai`, the LLM's confidence, the similarity, the reason, and `ai_model = llm.last_model`.
  - `no_link` / `not_enough_information`: status `dismissed`, with the reason.
  - No LLM or an `LLMError`: status `suggested` with `llm_checked=False` and the reason "Similarity 0.68; not checked by AI (…)". It is checked again on the next run.

### 7. Coverage: `backend/app/trace/coverage.py` (pure, unit-tested)
- **A link is live** when its status is accepted, or when it is explicit and not rejected (a manual link is accepted).
- **Test case match** (computed when read, so edited test cases are matched again). A live `test_function` link matches a live Step 8B test case when either:
  - the function's name or docstring mentions `TC-n`; or
  - the normalised name (without `test_`) equals the slug of the test case title, or their word sets overlap by at least 0.8 (Jaccard).
- **Levels:**
  - no live link: Not started;
  - implements only: Code only;
  - verifies only: Tests only;
  - both: Code + tests;
  - both, and every accepted criterion (status accepted/edited) has a live test case (its `criterion_id`) that matches a live test link: Verified.
- Each level comes with a short reason ("2 of 3 criteria have a linked test").
- The project summary is the count per level, plus pending AI suggestions.

### 8. API (owner only, mounted in [main.py](backend/app/main.py))
**`backend/app/api/repos.py`:**
- `GET /projects/{id}/repo` returns the repo, its URL, last analysed time, head SHA, `token_configured` and `last_result`; or null.
- `PUT /projects/{id}/repo {repository: "owner/repo" or a github.com URL}` validates the name, checks it on GitHub and stores the default branch.
- `DELETE /projects/{id}/repo` deletes the repo; its snapshots and links go with it (CASCADE).
- `POST /projects/{id}/repo/analyze` returns the `AnalysisResult`. A `ConnectorError` gives 502 with its message.
- `GET /projects/{id}/repo/snapshot` returns the counts, the recent commits and the PRs, for the Repository page.

**`backend/app/api/trace.py`:**
- `GET /projects/{id}/trace?module=&coverage=&link_type=&pending=` returns `{summary, rows:[{requirement, coverage, coverage_reason, criteria:[{code, covered}], implements:[link], verifies:[link]}]}`.
  - Each link has: its type, label, GitHub URL (`blob/{head}/{path}#L{line}`, `commit/{sha}`, `pull/{n}`), origin, status, confidence, reason, evidence and the matched test case codes.
  - Only approved requirements are shown; dismissed links are hidden.
- `POST /projects/{id}/trace/recover` re-runs link recovery on the stored snapshot, with no GitHub calls (e.g. after approving more requirements).
- `POST …/trace/links/{lid}/confirm` and `/reject` set the status, `decided_by` and `decided_at`. Confirm also works on a rejected link (to undo it).
- `POST …/trace/links {requirement_id, link_type, target_type, target_key}` adds a link: origin `user`, status `accepted`. The target must exist in the latest snapshot, commits or PRs (422 otherwise). The requirement must be approved.
- `GET …/trace/targets?q=&type=` searches the snapshot, commits and PRs, for the add-link picker.
- `GET …/trace/requirements/{rid}` returns the coverage and links of one requirement, for the panel.
- `GET …/trace/export.csv` uses `backend/app/reports/trace_export.py`, which reuses the CSV helpers of [test_case_export.py](backend/app/reports/test_case_export.py) (BOM, formula escape). It writes one row per requirement: code, statement, module, coverage, code links, test links (label [origin, status, confidence]), matched test cases, and criteria covered.
- Another owner's project, link or requirement gives 404.

## Frontend
Before using anything new, read `node_modules/next/dist/docs/` (AGENTS.md).
- **[lib/api.ts](frontend/lib/api.ts):** a "Repository and traceability" section with the types `Repo`, `AnalysisResult`, `RepoSnapshot`, `TraceLink`, `TraceRow`, `TraceSummary`, `Coverage` (+ labels and colours), and the functions `getRepo`, `connectRepo`, `disconnectRepo`, `analyzeRepo`, `getRepoSnapshot`, `getTrace(filters)`, `recoverLinks`, `confirmLink`, `rejectLink`, `addLink`, `searchTargets`, `getRequirementTrace` and `traceExportUrl`.
- **`components/repo/RepoCard.tsx`:** an owner/repo input with Connect / Disconnect, Analyze (busy state), "Last analysed 5 min ago · abc1234", a token badge (configured or not, never the token), and the last result's counts and notices.
  - It goes on the [project page](frontend/app/(dashboard)/projects/[id]/page.tsx) under the Jira fieldset.
  - The **Repository page** replaces its Step 10 placeholder with RepoCard plus read-only lists: file / test counts, recent commits, and PRs (merged badge), with GitHub links.
- **Traceability page** (`app/(dashboard)/traceability/page.tsx`, replaces the placeholder) is built from `components/trace/TraceabilityView.tsx` with `TraceRow.tsx`, `LinkChip.tsx`, `CoverageBadge.tsx` and `AddLinkDialog.tsx`.
  - **Summary bar:** the count per coverage level, and the pending suggestions.
  - **Toolbar:** "Find links again", Export CSV, and filters for module, coverage, link type and pending.
  - **Matrix rows:** requirement code + statement | coverage badge + reason + "AC 2/3" | Code chips (file / commit / PR) | Test chips (test file / function, matched TC codes).
  - **Highlighting:** rows with no test link get a distinct amber background (BUILD_PLAN "done when").
  - **Chips:** each shows origin (ID match / AI / manual), confidence, and a pending / confirmed / rejected style. The reason and evidence show on click. Each chip has Confirm and Reject, and opens on GitHub in a new tab.
  - **AddLinkDialog:** pick the link type, then search targets.
- **Requirement panel:** a "Trace links" section, `components/requirements/TraceLinksSummary.tsx`, like `TaskSummary.tsx`. It shows the coverage badge, links with GitHub links, Confirm and Reject, and "Open in Traceability". It is added in [RequirementPanel.tsx](frontend/components/requirements/RequirementPanel.tsx).

## Tests (respx for GitHub, FakeLLM, FakeEmbedder, no network)
- **Fixtures:** a small sample repo in `tests/fixtures/repo/` (code with REQ comments, tests with REQ names, docstrings and TC-n, a file with a syntax error, a venv folder), turned into a tarball in memory. JSON samples of GitHub responses in `tests/fixtures/api/github/`. `fake_llm.py` gains `_verify_links` (verdict per pair, `verify_links_fail`, `verify_link_calls`). `trace_helpers.py` reuses `seed_requirement` / `approve` from the earlier helpers.
- **`test_github_client.py`:**
  - commit paging stops at a known SHA and at the maximum;
  - commit files are fetched, and skipped when the budget is low (notice, `files_fetched=False`);
  - the PR cursor works;
  - a rate limit 403 with remaining 0 gives a clear message with the reset time;
  - no Authorization header without a token, and the token is never in errors;
  - a 404 gives a clear message;
  - the tarball size cap works.
- **`test_code_parser.py`:** test file and function detection (including `Test*` classes), symbols and line ranges, docstrings, comments, the syntax error fallback, skipped folders, and the file tree kept.
- **`test_trace_explicit.py`:**
  - ID regex variants, and negatives (`PREREQ-4`, `REQ-` without a number);
  - each source gives the right link type and target;
  - unapproved and unknown numbers are ignored;
  - a re-run makes no duplicates;
  - a rejected explicit link stays rejected.
- **`test_trace_suggest.py`:**
  - the threshold and top-k (with `vector_at`);
  - an LLM `link` is stored with its reason, confidence and `ai_model`;
  - `no_link` and `not_enough_information` are dismissed;
  - an LLM error or no LLM gives unchecked suggestions, which are checked on the next run;
  - **rejected links are not suggested again**;
  - accepted and user links are untouched;
  - a dismissed link is asked again after a new requirement version;
  - an unavailable embedder gives explicit links only, with the notice.
- **`test_trace_coverage.py`:** each of the 5 levels; Verified through a TC name match and through `TC-n`; no accepted criteria means not Verified; pending AI links do not count; explicit links count; a rejected explicit link does not count.
- **`test_trace_api.py`:**
  - connect (name and URL forms, a bad name gives 422, a GitHub 404 gives a clear error);
  - full analyse with respx;
  - **cache:** a second analysis with the same head downloads no tarball and asks only for new commits and PRs;
  - confirm, reject, undo, manual add (unknown target gives 422);
  - filters, recover, the panel endpoint, the CSV (columns, formula escape, Bangla);
  - another owner's project gives 404;
  - no response contains the token.
- **[test_migrations.py](backend/tests/test_migrations.py):** the new tables, CHECK constraints, unique keys, CASCADE, and downgrade.
- **RLS:**
  - a new test imports the migration module and checks that every table it creates is in `TABLES` and that its source runs `ENABLE ROW LEVEL SECURITY` under the postgresql guard;
  - [test_postgresql.py](backend/tests/test_postgresql.py) checks `relrowsecurity` on all 7 tables, plus a PostgreSQL flow (connect → analyse with respx → confirm → coverage → CSV).

## Verification
1. `cd backend && pytest -q`: the full suite is green.
2. `cd frontend && npm run lint && npm run build`.
3. **PostgreSQL:** Supabase is at `a1b2c3d4e5f6`. I will **ask you** before running `alembic upgrade head` (to `b2c3d4e5f6a7`). Then run `TEST_DATABASE_URL=… pytest tests/test_postgresql.py`.
4. **Manual walk-through:**
   1. connect a small public demo Python repo with `REQ-xxx` in commits and tests;
   2. Analyze: counts appear;
   3. Analyze again: "no new commits", and no tarball is downloaded;
   4. Traceability: explicit links appear at once, and AI suggestions show a reason;
   5. confirm and reject; "Find links again": the rejected link does not return;
   6. coverage badges change; rows with no test are amber;
   7. a requirement whose criteria all have matched tests shows Verified;
   8. the requirement panel shows the links;
   9. Export CSV.
5. Write `docs/traceability.md` the same way as `docs/tasks.md`. Save a memory note on the follow-ups (the Supabase revision, tarball rather than GitPython, Step 11 hooks).

At the end I give you a short summary of the new and changed files and how to test them.
