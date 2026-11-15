# Clarifications and approval (Step 8A)

How the team turns possible problems into questions for the client, collects the answers, and approves requirements.
Plan: [plans/step-8a-plan.md](plans/step-8a-plan.md).

## Flow

1. **Questions (AR-04).** In the requirement panel ("Suggest questions"), or for the whole project on the
   Clarifications page ("Suggest questions for all"), the LLM (`app/ai/prompts/clarification_questions.md`, via
   `app/ai/llm.py`, Groq first) writes 1–3 short, polite English questions per requirement. Only open findings of
   these kinds are used: possibly vague, possibly missing information, possible conflict, and possible change request.
   Each question is linked to the finding it resolves. When the model cannot write a useful question it answers
   "not enough information" and nothing is stored. Without a configured model the API answers 503, and the lead can
   still write questions by hand.
2. **Review.** Questions start as `suggested`. The lead can edit them (`edited`), delete drafts, or add their own
   (`origin = user`). "Suggest again" replaces only untouched AI suggestions that were never shared or answered
   (rule 2).
3. **Link (FR-22).** On the Clarifications page the lead ticks questions and creates a link. The link:
   - is shown **once**, because only a keyed hash of its token is stored (HMAC-SHA256 with `JWT_SECRET`);
   - expires after 7 days;
   - can be revoked;
   - works **once**: after one submission it is closed.

   **The app never sends anything to the client.** The lead copies the link and shares it.
4. **Client page.** `/clarify/<token>` needs no login, works on a phone, and shows only the project name and the
   chosen questions. Answers in English, Bangla or Banglish are stored exactly as typed. The page is rate limited
   per IP (30 requests a minute).
5. **Answers (FR-09).** Each answer is stored with its time, question, requirement and link. A "New answers" badge
   appears on the requirement row and on the Clarifications menu item. Opening the requirement marks its answers as
   seen.
6. **Proposed update.** "Propose update from answers" asks the LLM (`clarification_proposal.md`) for an updated
   statement from the answers that no accepted proposal has used yet, or for "not enough information". The lead can:
   - **Accept** it;
   - **Edit & accept** it, which creates a new version (`origin = user`, written by the lead) and runs validation
     again;
   - **Reject** it.

   A proposal made for an older version cannot be accepted.
7. **Approval.** "Approve" is separate from "Accept":
   - Only accepted or edited requirements can be approved.
   - Open High findings block approval unless the lead gives a reason. The reason and those finding ids are recorded
     in `requirement_approvals`.
   - An approved requirement cannot be rejected, merged or undone until the approval is removed.
   - Editing it creates a new version, which ends the approval.

## Notes

- The project owner is the team lead: only the owner can open a project.
- Changing `JWT_SECRET` closes every open clarification link, because their hashes no longer match.
- New tables: `clarification_questions`, `clarification_links`, `clarification_link_questions`,
  `clarification_answers`, `clarification_proposals` and `requirement_approvals` (migration `e9f0a1b2c3d4`). All of
  them have row level security on PostgreSQL.
