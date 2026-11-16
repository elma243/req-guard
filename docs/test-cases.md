# Acceptance criteria and test cases (Step 8B)

How the team turns approved requirements into acceptance criteria and Given/When/Then test cases, reviews them,
and exports them. Plan: [plans/step-8b-plan.md](plans/step-8b-plan.md).

## Flow

1. **Generate (AR-05).** Only approved requirements get test cases. Start it from the requirement panel
   ("Generate test cases") or for the whole project on the Test Cases page ("Generate for all approved"). The LLM
   (`app/ai/prompts/test_cases.md`, via `app/ai/llm.py`, Groq first) writes:
   - acceptance criteria (AC-1, AC-2 …);
   - 2–5 test cases (TC-1 …), each **positive**, **negative** or **edge**, with Given, When and Then, and linked to
     the criterion it verifies.

   Everything is in English, with cautious wording. When the requirement is too unclear to test, the model answers
   "not enough information" and nothing is stored. Too few usable test cases (fewer than 2) are treated the same way.
   Without a configured model the API answers 503, and the lead can still write test cases by hand.
2. **Generate again.** Only untouched AI suggestions (`origin = ai`, `status = suggested`) are replaced. Accepted,
   edited, rejected and hand-written items stay (rule 2). A suggested criterion that a kept test case links to also
   stays. Codes are never reused.

   "Generate for all approved" covers approved requirements that have no test cases yet, or whose untouched
   suggestions are marked "needs review". All the calls of one request share a 150-second limit; requirements not
   reached are listed, and running it again continues with them.
3. **Review.** On the Test Cases page, items are grouped by requirement. You can filter by status, type,
   requirement and "needs review" (`/test-cases?requirement=<id>` opens one requirement). For each item:
   - **Accept.** Accepting a test case also accepts its criterion if that is still a suggestion.
   - **Edit.** This saves a new version written by you (rule 6), shown under "History", and sets the status to
     `edited`.
   - **Reject.** A criterion cannot be rejected while test cases that are not rejected still verify it.
   - **Add by hand.** This works only on approved requirements; the item is saved as `origin = user`, `accepted`.
4. **Needs review.** When a requirement gets a new version (an edit, or an accepted clarification proposal), its
   approval ends and its criteria and test cases that are not rejected are marked "needs review", because they were
   written for the old text. Accepting or editing an item clears the mark. Removing the approval alone does not mark
   anything.
5. **Export (FR-10).** The Test Cases page has "Export CSV" and "Export PDF". Both list approved requirements with
   their accepted or edited criteria and test cases, the project name and the export date. Unreviewed suggestions and
   rejected items are left out, and "needs review" items are marked.
   - **CSV:** UTF-8 with a byte order mark, so Excel shows Bangla correctly. Cells that start with `= + - @` get a
     leading `'` so that a spreadsheet does not run them as formulas.
   - **PDF:** made with reportlab. Its built-in fonts cover Latin text only, so other characters (for example a
     Bangla project name) are shown as `?`. Requirements and test cases are in English.
   - **Wording:** the PDF says the list shows planned checks, not that the system is complete or ready to release.

## Notes

- New tables: `acceptance_criteria`, `acceptance_criterion_versions`, `test_cases` and `test_case_versions`.
  `requirements` gets `criterion_counter` and `test_case_counter` (migration `f0a1b2c3d4e5`). All the new tables have
  row level security on PostgreSQL.
- New backend package: `reportlab`.
- The requirement list and panel show `test_case_count`, `accepted_test_cases` and `test_cases_need_review`.
