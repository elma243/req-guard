# Traceability: repository analysis and trace links (Step 10)

How a project's GitHub repository is analysed, how approved requirements are linked to code and tests, and how
coverage is worked out. Plan: [plans/step-10-plan.md](plans/step-10-plan.md). Drift detection between two commits
is Step 11.

## Flow

1. **Connect.** On the project page (or the Repository page), enter `owner/repo` or the github.com address and
   press Connect. ReqGuard checks on GitHub that the repository exists and can be read, and stores its default
   branch. One repository per project; connecting another one removes the old one's analysis and links.
   - **Token.** `GITHUB_TOKEN` in `backend/.env` is optional for public repositories, but without it GitHub allows
     only 60 requests per hour. A private repository needs it. The token is only sent to api.github.com; the page
     only shows whether it is set.
2. **Analyze.** "Analyze" reads what is new through the GitHub REST API (`app/code/github.py`):
   - the head commit of the default branch;
   - new commits, newest first, stopping at the first one already stored (at most `GITHUB_MAX_COMMITS`, 200);
   - the changed files of each new commit (one request each, at most 100 per analysis). They are left for a later
     analysis when fewer than 50 requests are left this hour, and the page says so;
   - pull requests updated since the last analysis (title, description, branch, merged or not);
   - branch names;
   - **the code**: when the head commit has no snapshot yet, its tarball is downloaded once (at most
     `GITHUB_MAX_REPO_MB`, 50 MB). It is read in memory by `app/code/parser.py`: the file tree, and for Python files
     the functions, classes, tests (pytest naming), docstrings, comments and every requirement ID they mention. An
     unchanged head downloads nothing again. A file with a syntax error keeps its comments.
3. **Explicit links** (`app/trace/explicit.py`, plain code). The ID of an **approved** requirement (REQ-004,
   req_4, test_req_004_login) found next to an item links them, with confidence 1.0:

   | Where the ID is | Link |
   |---|---|
   | commit message | implements the commit |
   | pull request title, description or branch | implements the pull request |
   | branch without a pull request | implements the branch's head commit |
   | comment or docstring in a code file | implements the file |
   | test name, its docstring or a comment inside it | verifies the test function |
   | elsewhere in a test file | verifies the test file |

   IDs of unknown or unapproved requirements are ignored.
4. **AI suggestions** (`app/trace/suggest.py`):
   - **Similarity.** The requirement statement is compared with each code file (path, function and class names,
     module docstring), test function (name, docstring, path), commit message and pull request (title and
     description), with all-MiniLM-L6-v2 embeddings. Target embeddings are cached by a hash of their text.
   - **Candidates.** Per requirement and kind of item, the 3 most similar items with similarity at least
     `TRACE_SIMILARITY` (0.6) become candidates.
   - **LLM check.** The LLM checks the candidates (`app/ai/prompts/verify_links.md`, through `app/ai/llm.py`):
     - "link" is kept as a suggestion with a one-sentence reason, a confidence and the model's name;
     - "no link" and "not enough information" are kept as *dismissed* and not shown.
   - **No LLM.** Without an answering LLM, a candidate is still suggested, marked "not checked by AI", and checked on
     the next analysis.
   - **Without the embedding model,** only explicit links are made, and the page says why.
5. **Decide** (Traceability page or the requirement panel). Click a link to see its reason and evidence, then
   Confirm or Reject it, or open it on GitHub. "+ Link" adds a link by hand, picked from the analysed files, tests,
   commits and pull requests.
   - **Rule 2.** A person's decision is never changed. A rejected item is never suggested again for that
     requirement.
   - **Asked again.** A dismissed item is asked again only after the requirement gets a new version.
   - **Upgrade.** An AI suggestion nobody decided on becomes explicit when its ID is later found.
   - **Find links again** re-runs steps 3 and 4 on the stored analysis, without calling GitHub (for example after
     approving more requirements).
6. **Coverage** (`app/trace/coverage.py`). A link counts when a person confirmed or added it, or when it is explicit
   and not rejected. AI suggestions count only once confirmed.

   | Level | Meaning |
   |---|---|
   | Not started | no counted link |
   | Code only | code links, no test link: possible test gap |
   | Tests only | test links, no code link: possible traceability gap |
   | Code + tests | both; with acceptance criteria, "k of n criteria have a linked test" |
   | Verified | both, and every accepted acceptance criterion has a Step 8B test case matched by a counted test function link |

   - **Test case match.** A test function matches a test case when its name or docstring mentions it (TC-2), or when
     its name (without `test_` and requirement IDs) is the test case title as words, or shares at least 80 % of
     the words.
   - **No criteria, no Verified.** A requirement without accepted criteria cannot be Verified.
   - **Reading the test code** to confirm a criterion is Step 11.
7. **Matrix and export.** The Traceability page shows:
   - one row per approved requirement: coverage, criteria with a test, code links, test links;
   - counts per level and the AI suggestions waiting;
   - filters for module, coverage, link type and "AI suggestions to review".

   Rows without a counted test link have an amber background. "Export CSV" writes one row per requirement, with its
   links (origin, status, confidence), the matched test cases and the criteria covered. The file is UTF-8 with a
   byte order mark, and its cells are escaped against formulas.

## Notes

- **New tables** (migration `b2c3d4e5f6a7`), all with row level security on PostgreSQL: `repositories`,
  `repo_snapshots`, `repo_files`, `repo_commits`, `repo_pulls`, `trace_target_embeddings` and `trace_links`.
  `tests/test_rls.py` checks that every table a migration creates gets RLS in that migration.
- **New env variables:** `GITHUB_MAX_COMMITS`, `GITHUB_MAX_REPO_MB`, `TRACE_SIMILARITY` (see `.env.example`). There
  are no new packages: the tarball is read with the standard library, not GitPython, so the server needs no git.
- **Link keys.** Links point at stable keys (path, `path::Class.test_name`, commit SHA, PR number), so decisions
  survive re-analysis. A link whose file or test is gone from the last analysed commit is marked "removed".
- **Requirement panel:** a "Trace links" section shows the coverage and the links, with Confirm and Reject.
