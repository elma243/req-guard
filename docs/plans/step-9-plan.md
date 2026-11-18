# Step 9: requirement grouping and assignee suggestion (FR-11, AR-06)

## Context
After Step 8 the lead has approved requirements, each with test cases, but nothing yet says who builds what. This step lets the lead:
- group approved requirements into task groups: by the module that validation already matched, otherwise by embedding clusters that the AI names. Each grouping shows its reason;
- get one task per requirement, linked to the requirement and its test cases;
- get a suggested assignee for each task, from skill match and current workload, with a short reason such as "Python, FastAPI skills; 2 open tasks". Nothing is ever assigned automatically;
- confirm, change or reject each suggestion, or "Accept all suggestions"; then move tasks through assigned → in progress → done;
- see tasks marked "needs review" when their requirement is edited or loses its approval;
- export tasks as CSV, and push them to Jira as issues, but only when the lead clicks.

**Decisions** (from your answers and from reading the code):
- **Jira target:** a new project setting, "Jira project for tasks" (`jira_task_project_key`) with an issue type (`jira_task_issue_type`, default `Task`). It is edited on the project page, so dev tasks stay apart from the client's Jira project. Push reuses the Jira connector's credentials and HTTP helper (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`); there are no new env variables.
- **Skill match:** words + embeddings.
  - A skill matches when its words, or a synonym from a small list, appear in the task text.
  - It also matches when the embedding similarity between the skill and the task text is at least 0.45.
  - If the embedding model cannot load, words alone are used, and the result says so.
- **No new packages.** scikit-learn (`AgglomerativeClustering`) and sentence-transformers are already in `requirements.txt`.
- **Grouping only adds, it never overwrites (rule 2).** A run creates tasks only for approved requirements that have no task yet. A new task joins an existing group when that group fits: the same module, or a similar cluster. Groups and tasks the lead touched are never changed.
- **The AI only names groups.** It is optional: without an LLM, a group gets a name built from its most frequent words, and that is shown as "named by rule". Without the embedding model, the unmatched requirements go to one "No matching module" group, with that as the reason.
- **"Accept all suggestions"** skips tasks marked "needs review"; those need a look first.
- **Needs review** is set by `add_user_version` (an edit or an accepted proposal) and by "Remove approval". The lead clears it with "Mark reviewed".
- The project owner is the lead (`OwnedProject`), as in Step 8.

## Branch and commits
1. `git checkout -b step-9-grouping`. Save this plan as `docs/plans/step-9-plan.md` and commit "Add Step 9 plan".
2. Make small commits, roughly:
   1. models and migration
   2. grouping
   3. assignee suggestion and workload
   4. tasks API (review, status, needs review)
   5. CSV export
   6. Jira push and the project setting
   7. backend tests for each
   8. frontend API and types
   9. Tasks page
   10. project page Jira setting
   11. docs
3. No Co-Authored-By lines. No push.

## Data model (migration `a1b2c3d4e5f6_tasks`, down_revision `f0a1b2c3d4e5`)
Models go in `backend/app/models/task.py` and are registered in `models/__init__.py`. Both new tables run `ALTER TABLE … ENABLE ROW LEVEL SECURITY` under `if op.get_bind().dialect.name == "postgresql":`, as in [f0a1b2c3d4e5_test_cases.py](backend/alembic/versions/f0a1b2c3d4e5_test_cases.py). Downgrade drops both tables and the new project columns.

| Table | Columns (besides id, created_at, updated_at) |
|---|---|
| `task_groups` | `project_id` FK CASCADE; `name`; `kind` module\|cluster\|unmatched (CHECK); `module_name` (nullable); `reason`; `origin` ai\|user; `status` suggested\|accepted\|edited (CHECK; renaming sets `edited`); `ai_model` |
| `tasks` | `project_id` FK CASCADE; `group_id` FK `task_groups` CASCADE; `requirement_id` FK `requirements` CASCADE, **unique** (one task per requirement); `number` (T-001, from `projects.task_counter`); `status` suggested\|assigned\|in_progress\|done (CHECK); `grouping_reason`; `group_origin` ai\|user (user after a move); `assignee_id` FK `team_members` SET NULL; `suggested_assignee_id` FK `team_members` SET NULL; `alternative_assignee_id` FK SET NULL; `suggestion_reason`; `suggestion_score` float; `suggestion_status` suggested\|accepted\|changed\|rejected (nullable, CHECK); `rejected_member_ids` JSON list; `needs_review` bool; `needs_review_reason`; `requirement_version` (the version it was made for); `jira_issue_key`; `jira_pushed_at` |

`projects` gets `task_counter` (int, default 0), `jira_task_project_key` (nullable) and `jira_task_issue_type` (default "Task").

`Requirement` gets a `task` relationship (uselist=False).

Rule 2 here means: `assignee_id` is set only by the lead's action. The AI writes only the `suggested_*` fields.

## Backend

### 1. Grouping (FR-11): new `backend/app/nlp/cluster.py` (pure functions) and `backend/app/tasks/grouping.py`, prompt `app/ai/prompts/group_names.md`
- **Candidates:** approved requirements (`approval is not None`) that have no task.
- **Module match first:** a requirement with `module_name` joins the group of kind `module` with that module name, which is created if missing. The reason reads "Matches module 'Payments' (score 0.62)". The group reason reads "A module of the project".
- **Unmatched requirements:**
  1. Embed them with `similarity.get_embedder()` + `ensure_embeddings` (from [similarity.py](backend/app/nlp/similarity.py)).
  2. Try existing `cluster` groups first: the requirement joins the one whose average similarity to its tasks is at least `JOIN_SIMILARITY = 0.5`.
  3. Cluster the rest with `AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average", distance_threshold=0.5)`. One requirement alone is its own cluster.
  4. A cluster of 2 or more becomes a new `cluster` group. The task reason reads "No module matched; similar to REQ-003, REQ-007 (similarity 0.71)".
  5. Singletons go to one `unmatched` group, "Other requirements". The reason reads "No module matched and not similar to other unmatched requirements".
- **Naming:** one LLM call (`OptionalLLM`) for all new clusters: `{clusters:[{ref, statements}]}` → `{groups:[{ref, name (≤ 5 words), reason}]}`, validated by Pydantic.
  - An unknown ref, an empty name or `not_enough_information` falls back to the rule name: the 3 most frequent content words.
  - An `LLMError` also falls back, and the result shows the error as a notice. The grouping itself never fails because of the LLM.
- **Result:** `{created_tasks, created_groups, items:[{requirement code, group name, reason}], notice}`.
- **Pure helpers in `nlp/cluster.py`:** `cluster(vectors, threshold) -> labels` and `rule_name(statements)`. They are unit-tested without a database.

### 2. Assignee suggestion (AR-06): `backend/app/tasks/assign.py`, synonyms in `backend/app/nlp/skill_words.py` (a Python dict, like [lexicon.py](backend/app/nlp/lexicon.py))
- **Task text:** the current statement, actor, group name, module name and module description.
- **Skill match per member:**
  - each skill matches by words: all of its words appear as whole words in the task text, or a synonym does (e.g. backend ↔ api/server, frontend ↔ page/screen/ui, database ↔ sql/postgresql, testing ↔ qa/test);
  - or by embedding: cosine between the skill and the task text is at least 0.45;
  - `skill_score` = the best matched similarity (a word match counts as 1.0), plus 0.1 for each further matched skill, capped at 1.
- **Workload:** the member's open tasks (`assigned` + `in_progress`) in the project, plus the suggestions already given to them in the same run. That way one run spreads the suggestions instead of giving everything to one person.
- **Score:** `0.7 × skill_score + 0.3 × 1/(1 + open_tasks)`. Members in `rejected_member_ids` are left out. The best member is stored as the suggestion and the second as the alternative (BUILD_PLAN: top 2).
- **Reason:** "Python, FastAPI skills; 2 open tasks", or "No matching skills; 0 open tasks (lowest workload)". "(skills matched by words only)" is added when the embedding model was unavailable.
- **When it runs:** right after grouping, for new tasks; for "Suggest assignees" (tasks with no assignee and no pending suggestion); and for "Suggest again" on one task.
- **Never:** it never runs on a task that has an assignee. A project without members gets the notice "Add team members to get suggestions".

### 3. Router `backend/app/api/tasks.py` (owner only, mounted in [main.py](backend/app/main.py))
- **Grouping and suggestions:**
  - `POST /projects/{id}/tasks/group` runs grouping, then suggestion for the new tasks.
  - `POST /projects/{id}/tasks/suggest` suggests for unassigned tasks without a pending suggestion.
  - `POST /projects/{id}/tasks/accept-suggestions` accepts all pending suggestions, skips the ones that need review, and returns how many were accepted and skipped.
- `GET /projects/{id}/tasks?member_id=&status=&module=&needs_review=` returns:
  - `groups:[{id, name, kind, module_name, reason, origin, status, tasks:[…]}]`;
  - `workload:[{member, skills, assigned, in_progress, done, pending_suggestions}]`;
  - `jira:{configured, project_key}`.
  - **Each task carries:** its code; the requirement's code, statement and approval; the counts of live and accepted test cases with their codes; the grouping reason; the assignee and the suggestion (member, alternative, reason, status); status; needs review with its reason; the Jira key and URL.
- **Single task:**
  - `POST …/tasks/{tid}/suggestion/accept` sets the assignee to the suggestion, status `assigned`, and suggestion status `accepted`.
  - `POST …/tasks/{tid}/assign {member_id}` sets assignee to the chosen member, status `assigned`, and suggestion status `changed` (if there was one). The member must belong to the project, or the API gives 404.
  - `POST …/tasks/{tid}/suggestion/reject` sets suggestion status `rejected` and adds the member to `rejected_member_ids`. Status stays `suggested`.
  - `POST …/tasks/{tid}/suggest` suggests again.
  - `PATCH …/tasks/{tid} {status?, group_id?}`:
    - a status other than `suggested` needs an assignee (409);
    - moving the task to another group sets `group_origin=user`, and an empty AI group is removed;
    - setting status `suggested` removes the assignee (unassign).
  - `POST …/tasks/{tid}/reviewed` clears `needs_review`.
- `PATCH /projects/{id}/task-groups/{gid} {name}` renames a group: status `edited`, origin `user`.
- **Errors:** another owner's project, task or member gives 404.

### 4. Needs review, in [requirements.py](backend/app/api/requirements.py)
- `add_user_version` sets `needs_review` and the reason "Requirement changed (v3)" on the task, next to `mark_for_review`.
- `unapprove_requirement` sets `needs_review` and the reason "Approval removed".
- `_out` gains `task: {code, status, assignee_name}` for the requirement panel.
- The task keeps its group and assignee. Grouping does not touch it again (the requirement already has a task).

### 5. Export: `backend/app/reports/task_export.py`
- `GET /projects/{id}/tasks/export.csv` follows `build_csv` in [test_case_export.py](backend/app/reports/test_case_export.py) (reuse `_cell` / `export_filename`; make them public):
  - UTF-8 with a BOM;
  - the rows `Project` and `Exported`, then one row per task: group, module, task code, requirement code, statement, status, assignee, suggested assignee, suggestion reason, needs review, test cases (accepted codes), Jira key;
  - formula escape on every cell.

### 6. Jira push: `push_issues` in `backend/app/connectors/jira.py` + `POST /projects/{id}/tasks/jira-push {task_ids}`
- **When it can run:**
  - only on the user's click, through the dialog that lists the tasks;
  - only tasks with an assignee and no `jira_issue_key`; others are reported as skipped.
  - Without the project key or the Jira env, the API gives 409 "Set the Jira project for tasks on the project page" or "Not configured: set JIRA_… in backend/.env". Tokens never reach the frontend.
- **Each task:**
  - `POST /rest/api/3/issue` through `send()` from [http.py](backend/app/connectors/http.py);
  - summary "REQ-004: statement" (cut to 255 characters);
  - an ADF description with the statement, group, assignee name in text (Jira needs account ids, so the name is not mapped to a Jira user), accepted criteria and accepted test case titles, and "Created by ReqGuard".
- **Result:** the returned issue key is stored at once (commit after each issue), so a later error does not lose the earlier keys. The result is `{pushed:[{task, key}], skipped, error}`.
- **Project setting:** `ProjectUpdate`/`ProjectOut` gain `jira_task_project_key` (validated like `JiraSettings.project_key`) and `jira_task_issue_type`.

## Frontend
- **Reading first:** read `node_modules/next/dist/docs/` for anything new used (as AGENTS.md asks).
- **[lib/api.ts](frontend/lib/api.ts):** a "Tasks" section with the types `Task`, `TaskGroup`, `MemberWorkload`, `GroupResult` and `JiraPushResult`, and functions `groupRequirements`, `suggestAssignees`, `acceptAllSuggestions`, `listTasks(filters)`, `acceptSuggestion`, `assignTask`, `rejectSuggestion`, `suggestAgain`, `updateTask`, `markTaskReviewed`, `renameTaskGroup`, `pushTasksToJira` and `taskExportUrl`. `Project` gains the two Jira fields, and `Requirement` gains `task`.
- **Tasks page:** `app/(dashboard)/tasks/page.tsx` replaces the placeholder and uses `components/tasks/TasksView.tsx` plus small parts (`WorkloadSummary.tsx`, `TaskRow.tsx`, `JiraPushDialog.tsx`).
  - **Toolbar:** "Group approved requirements", "Suggest assignees", "Accept all suggestions", Export CSV (link) and Push to Jira. There are filters for member, status, module and needs review.
  - **Workload summary:** one card per member, with skills, assigned / in progress / done, and pending suggestions.
  - **Groups:** shown as a list. Each group has its name (rename inline), kind badge (Module / AI cluster / Other), reason, and an "AI-named" or "named by rule" badge.
  - **Task rows:**
    - the requirement code (link to the requirement) and statement;
    - test cases "3 accepted / 4", with a link to `/test-cases?requirement=<id>`;
    - the grouping reason;
    - the suggestion card: member + reason + alternative, with Accept / Change (member select) / Reject / Suggest again;
    - the assignee and a status select;
    - a "Needs review" badge with its reason and "Mark reviewed";
    - the Jira key as a link;
    - "Move to group" in a small menu.
  - **Jira push dialog:** shows the target project key and a checklist of assigned, unpushed tasks, with the button "Create N Jira issues". Nothing is sent before that click.
  - **Notices:** group results, AI naming fallback, and model unavailable.
- **Project page:** a "Jira project for tasks" field (key + issue type) next to the modules.
- **Requirement panel:** a one-line "Task T-004 · assigned to Rahim" with a link to `/tasks`.

## Tests (mocked LLM, mocked Jira with respx, fake embedder, no real APIs)
- **Fake LLM:** [tests/fake_llm.py](backend/tests/fake_llm.py) answers `group_names`, with overrides, `group_names_fail` and `group_name_calls`. A `tasks_helpers.py` reuses `seed_requirement` and `use_llm` from [clarify_helpers.py](backend/tests/clarify_helpers.py), plus `approve`, `set_modules` and `add_member`.
- **`test_cluster.py`** (pure): similar vectors cluster together, singletons stay apart, the rule name uses the most frequent words, and one input is handled.
- **`test_task_grouping.py`:**
  - module-matched requirements go to their module group with the reason;
  - unmatched similar ones form an AI-named cluster (FakeEmbedder vectors);
  - singletons go to "Other requirements";
  - unapproved requirements are ignored;
  - a second run adds only new requirements and joins existing groups;
  - renamed and moved items are untouched;
  - LLM errors and no LLM fall back to rule names;
  - an unavailable embedder puts everything in one unmatched group with the reason;
  - another owner's project gives 404.
- **`test_task_suggestion.py`:**
  - a word match and a synonym match ("API" task → "backend" skill);
  - an embedding match;
  - workload breaks a tie and spreads suggestions within one run;
  - the reason text is exactly "Python, FastAPI skills; 2 open tasks";
  - the alternative is the second best;
  - a rejected member is not suggested again;
  - nothing happens on a task with an assignee;
  - no members gives the notice;
  - words only when the embedder is unavailable.
- **`test_task_review.py`:**
  - accept, change and reject;
  - "Accept all" skips needs-review tasks;
  - status flow, with 409 for a status without an assignee;
  - move and rename;
  - a foreign member gives 404;
  - workload counts in the list;
  - each filter.
- **`test_task_needs_review.py`:**
  - an edit and an accepted proposal mark the task;
  - "Remove approval" marks it;
  - "Mark reviewed" clears it;
  - grouping does not create a second task after re-approval.
- **`test_task_export.py`:** the CSV columns and rows, formula escape, and a Bangla name in UTF-8.
- **`test_task_jira_push.py`** (respx):
  - the request body (project key, issue type, summary, ADF description);
  - the key is stored;
  - an already pushed or unassigned task is skipped;
  - an error mid-way keeps the earlier keys;
  - no project key gives 409;
  - missing env gives 409;
  - a 401 from Jira gives a clear message.
- **[test_migrations.py](backend/tests/test_migrations.py):** the new tables, CHECK constraints, the unique `requirement_id`, the on-delete rules and downgrade.
- **[test_postgresql.py](backend/tests/test_postgresql.py):** RLS is enabled on `task_groups` and `tasks`, and the flow runs on PostgreSQL: approve → group → suggest → accept all → status → export CSV.

## Verification
1. `cd backend && pytest -q`: everything green.
2. **PostgreSQL:** Supabase is at `f0a1b2c3d4e5`. I will **ask you** before running `alembic upgrade head` (to `a1b2c3d4e5f6`) on Supabase. Then `TEST_DATABASE_URL=<DATABASE_URL> pytest tests/test_postgresql.py`.
3. `cd frontend && npm run lint && npm run build`.
4. **Manual walk-through:**
   1. add members with skills and modules;
   2. approve some requirements;
   3. "Group approved requirements": groups with reasons and suggestions with reasons appear;
   4. accept one suggestion, change one, reject one; "Accept all";
   5. move a task to in progress, then done; the workload summary updates;
   6. edit an approved requirement: its task shows "needs review"; "Mark reviewed";
   7. Export CSV;
   8. set the Jira project, Push to Jira (with the demo Jira site), and the keys appear.
5. Write `docs/tasks.md`, the same way as `docs/test-cases.md`. `.env.example` needs no new variables. Save a memory note on the follow-ups (the Supabase revision, Jira assignee not mapped).
