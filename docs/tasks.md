# Tasks: grouping and assignee suggestion (Step 9)

How approved requirements become task groups and tasks, how an assignee is suggested, and how the lead confirms
it. Plan: [plans/step-9-plan.md](plans/step-9-plan.md).

## Flow

1. **Group (FR-11).** On the Tasks page, "Group approved requirements" makes one task (T-001 …) for each approved
   requirement that has no task yet. Each task shows why it is in its group.
   - **Module first.** A requirement whose module (found by validation, Step 7) is still one of the project's
     modules joins that module's group: "Matches module 'Payments' (score 0.62)."
   - **Then similar requirements.** The other requirements are embedded (all-MiniLM-L6-v2):
     - a requirement joins an existing group of similar requirements when its average similarity to that group's
       tasks is at least 0.5;
     - the rest are clustered with `AgglomerativeClustering` (cosine distance, average linkage, cut at 0.5);
     - a cluster of two or more becomes a new group, which the LLM names in one call
       (`app/ai/prompts/group_names.md`): "No module matched; similar to REQ-004 (similarity 0.90)";
     - a requirement similar to none goes to "Other requirements".
   - **No AI, or no embeddings.** Without an LLM, or when it fails, a group is named from its most frequent words
     and is marked "Rule-based". Without the embedding model, unmatched requirements all go to "Other requirements",
     and the page says why.
   - **Grouping only adds (rule 2).** Existing tasks and groups are never changed, so the lead's renames and moves
     stay.
2. **Suggest an assignee (AR-06).** Right after grouping (and with "Suggest assignees" or a task's "Suggest"), each
   unassigned task gets a suggested member, the next-best member, and a short reason such as
   "Python, FastAPI skills; 2 open tasks".
   - **Skills.** A skill matches when its words, or a synonym from `app/nlp/skill_words.py` (for example
     backend ↔ API, frontend ↔ screen, security ↔ password), appear in the task text. The task text is the
     statement, its actor, the group name and the module's description. A skill also matches when its embedding
     similarity to the task text is at least 0.45. Without the embedding model, words alone are used, and the reason
     says "skills matched by words only".
   - **Workload.** This counts the member's open tasks (assigned or in progress), plus the suggestions given to them
     earlier in the same run, so that one run spreads the work.
   - **Score.** `0.7 × skill + 0.3 / (1 + open tasks)`. A member the lead rejected for a task is not suggested for it
     again.
   - **Never assigned automatically.** A suggestion is only a suggestion until the lead acts.
3. **Decide.** For each task the lead can:
   - **Accept** the suggestion;
   - **Pick someone else**, which is recorded as "changed";
   - **Reject** the suggestion.

   "Accept all suggestions" accepts every pending suggestion except on tasks marked "needs review". After that, the
   status moves through **assigned → in progress → done**; "Unassign" returns the task to not assigned. A task can
   be moved to another group, and a group can be renamed.
4. **Needs review.** The task is marked "needs review" when its requirement gets a new version (an edit, or an
   accepted clarification proposal) or loses its approval. It keeps its group and assignee. "Mark reviewed" clears
   the mark. Grouping never makes a second task for the same requirement.
5. **Workload summary and filters.** The Tasks page shows, per member, their skills and their open, in progress,
   done and suggested tasks. The board can be filtered by member (assignee or pending suggestion), status, module
   and "needs review".
6. **Export.** "Export CSV" lists every task with:
   - its group, module, requirement and statement;
   - its status and assignee;
   - the pending suggestion and its reason;
   - "needs review";
   - the accepted test cases;
   - the Jira key.

   The file is UTF-8 with a byte order mark, and its cells are escaped against formulas.
7. **Push to Jira.** Set "Jira project for tasks" (a key such as DEV, and an issue type, default Task) on the
   project page. It can differ from the Jira project the client messages come from.
   - **Sending.** "Push to Jira…" opens a list of assigned tasks that are not in Jira yet. Issues are created only
     when "Create N Jira issues" is clicked.
   - **The issue.** Each issue has the summary "REQ-004: statement". Its description lists the requirement, the
     group, the assignee's name, and the accepted criteria and test cases.
   - **The key.** The issue key is saved on the task at once, so an error part-way keeps the earlier keys, and a
     task is never sent twice.
   - **Credentials.** They come from `JIRA_BASE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN` in `backend/.env`. They are
     never sent to the frontend.

## Notes

- **New tables:** `task_groups` and `tasks` (migration `a1b2c3d4e5f6`), both with row level security on
  PostgreSQL. `projects` gets `task_counter`, `jira_task_project_key` and `jira_task_issue_type`.
- **No new packages and no new env variables.**
- **Jira assignee:** Jira needs an account id to assign an issue, so ReqGuard writes the assignee's name in the
  description and leaves the Jira assignee empty.
- **Requirement panel:** a "Task" line shows the task code, status, assignee and "needs review".
- **Removing a team member:** their assigned and in-progress tasks go back to "not assigned", and their pending
  suggestions are cleared, so "Suggest assignees" can suggest someone else.
