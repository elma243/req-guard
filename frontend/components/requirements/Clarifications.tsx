"use client";

// Clarification part of the requirement panel (Step 8A): the questions for the client (AI-suggested, editable),
// the client's answers as a Q&A thread, and the AI's proposed update from those answers, which the lead accepts,
// edits or rejects. Nothing is sent to the client from here: questions are shared from the Clarifications page.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { dangerButton, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  MAX_QUESTION_CHARS,
  acceptProposal,
  addQuestion,
  deleteQuestion,
  editQuestion,
  generateQuestions,
  getRequirement,
  markAnswersSeen,
  proposeUpdate,
  rejectProposal,
  type ClarificationQuestion,
  type Proposal,
  type RequirementDetail,
} from "@/lib/api";
import { answersChanged } from "@/lib/newAnswers";
import { wordDiff } from "@/lib/wordDiff";
import { CATEGORY_LABELS } from "./ValidationFindings";
import { formatTime } from "./labels";

type Notice = (kind: "info" | "error", text: string) => void;

const STATE_STYLE: Record<ClarificationQuestion["state"], string> = {
  draft: "text-zinc-400 ring-white/10",
  shared: "text-sky-300 ring-sky-400/30",
  answered: "text-emerald-300 ring-emerald-400/30",
};

export default function Clarifications({
  projectId,
  detail,
  disabled,
  onDetail,
  notify,
}: {
  projectId: string;
  detail: RequirementDetail;
  disabled: boolean;
  onDetail: (updated: RequirementDetail) => void;
  notify: Notice;
}) {
  const [working, setWorking] = useState<string | null>(null);
  const busy = disabled || working !== null;
  const closed = detail.status === "rejected" || detail.status === "merged";

  // Answers that were new when the panel opened keep their "New" mark while it stays open
  const [newIds] = useState(() => new Set(detail.answers.filter((a) => a.seen_at === null).map((a) => a.id)));
  const marked = useRef(false);
  const onSeen = useRef(onDetail);
  useEffect(() => {
    onSeen.current = onDetail;
  });
  useEffect(() => {
    if (marked.current || !detail.answers.some((a) => a.seen_at === null)) return;
    marked.current = true;
    markAnswersSeen(projectId, detail.id)
      .then((updated) => {
        onSeen.current(updated);
        answersChanged();
      })
      .catch(() => {
        marked.current = false; // tried again next time
      });
  }, [projectId, detail.id, detail.answers]);

  // True when it worked; errors become a toast
  async function run(label: string, action: () => Promise<RequirementDetail | void>, done?: string) {
    if (busy) return false;
    setWorking(label);
    try {
      const updated = (await action()) || (await getRequirement(projectId, detail.id));
      onDetail(updated);
      if (done) notify("info", done);
      return true;
    } catch (err) {
      notify("error", errorText(err));
      return false;
    } finally {
      setWorking(null);
    }
  }

  function generate() {
    void run("generate", async () => {
      const result = await generateQuestions(projectId, detail.id);
      const item = result.items[0];
      if (!item) notify("info", "No open findings that a question could resolve.");
      else if (item.not_enough_information) notify("info", `Not enough information to write a good question for ${detail.code}.`);
      else notify("info", `${item.created} question${item.created === 1 ? "" : "s"} suggested. Review them before sharing.`);
    });
  }

  const used = new Set(
    detail.proposals.filter((p) => p.status === "accepted" || p.status === "edited").flatMap((p) => p.answer_ids),
  );
  const unusedAnswers = detail.answers.filter((a) => !used.has(a.id)).length;
  const openProposal = detail.proposals.findLast((p) => p.status === "suggested") ?? null;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-medium text-zinc-200">Questions for the client</h4>
          {!closed && !detail.approval && (
            <button type="button" onClick={generate} disabled={busy} className={`${secondaryButton} ml-auto !px-2.5 !py-1 text-xs`}>
              {working === "generate" ? "Writing…" : detail.questions.length ? "Suggest again" : "Suggest questions"}
            </button>
          )}
        </div>
        {detail.questions.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-500">
            No questions yet. Questions are suggested for possibly vague, incomplete or conflicting wording.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {detail.questions.map((q) => (
              <QuestionItem
                key={q.id}
                question={q}
                disabled={busy}
                onSave={(text) => run("edit", async () => void (await editQuestion(projectId, q.id, text)), "Question saved")}
                onDelete={() => run("delete", async () => void (await deleteQuestion(projectId, q.id)), "Question deleted")}
              />
            ))}
          </ul>
        )}
        {!closed && (
          <AddQuestion
            disabled={busy}
            onAdd={(text) => run("add", async () => void (await addQuestion(projectId, detail.id, text)), "Question added")}
          />
        )}
        {detail.questions.some((q) => q.state === "draft") && (
          <p className="mt-2 text-xs text-zinc-500">
            Share questions from the{" "}
            <Link href="/clarifications" className="text-indigo-300 hover:underline">
              Clarifications
            </Link>{" "}
            page. Nothing is sent to the client automatically.
          </p>
        )}
      </div>

      {detail.answers.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-zinc-200">Client answers</h4>
          <ul className="mt-2 space-y-2">
            {detail.answers.map((a) => (
              <li key={a.id} className="rounded-lg border border-white/10 p-3">
                <p className="text-xs text-zinc-400">Q: {a.question_text}</p>
                <p className="mt-1 whitespace-pre-line text-sm text-zinc-100">{a.text}</p>
                <p className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
                  <span className="tabular-nums">{formatTime(a.answered_at)}</span>
                  {newIds.has(a.id) && (
                    <span className="rounded-full bg-indigo-400/15 px-1.5 text-indigo-200 ring-1 ring-indigo-400/40">New</span>
                  )}
                </p>
              </li>
            ))}
          </ul>
          {!closed && !openProposal && (
            <button
              type="button"
              disabled={busy || unusedAnswers === 0}
              onClick={() => run("propose", () => proposeUpdate(projectId, detail.id))}
              className={`${secondaryButton} mt-2`}
              title={unusedAnswers === 0 ? "Every answer was already used in an update" : undefined}
            >
              {working === "propose" ? "Proposing…" : "Propose update from answers"}
            </button>
          )}
        </div>
      )}

      {openProposal && !closed && (
        <ProposalCard
          key={openProposal.id}
          proposal={openProposal}
          current={detail.current.statement}
          approved={detail.approval !== null}
          disabled={busy}
          onAccept={(statement) =>
            run(
              "accept",
              () => acceptProposal(projectId, detail.id, openProposal.id, statement),
              "Saved as a new version and checked again",
            )
          }
          onReject={() => run("reject", () => rejectProposal(projectId, detail.id, openProposal.id))}
        />
      )}
    </div>
  );
}

function QuestionItem({
  question: q,
  disabled,
  onSave,
  onDelete,
}: {
  question: ClarificationQuestion;
  disabled: boolean;
  onSave: (text: string) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(q.text);
  const origin = q.origin === "user" ? "Yours" : q.status === "edited" ? "AI, edited" : "AI-suggested";

  return (
    <li className="rounded-lg border border-white/10 p-3">
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void onSave(text.trim()).then((ok) => ok && setEditing(false));
          }}
        >
          <textarea
            aria-label="Question text"
            autoFocus
            rows={3}
            maxLength={MAX_QUESTION_CHARS}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className={inputClass}
          />
          <div className="mt-2 flex gap-2">
            <button type="submit" disabled={disabled || !text.trim()} className={primaryButton}>
              Save
            </button>
            <button type="button" onClick={() => setEditing(false)} className={secondaryButton}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <p className="text-sm text-zinc-100">{q.text}</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <span className={`rounded-full px-2 py-0.5 capitalize ring-1 ${STATE_STYLE[q.state]}`}>{q.state}</span>
        <span>{origin}</span>
        <span>
          ·{" "}
          {q.finding_category
            ? CATEGORY_LABELS[q.finding_category]
            : q.origin === "ai"
              ? "Finding resolved"
              : "No finding"}
        </span>
        {!editing && q.state !== "answered" && (
          <span className="ml-auto flex gap-1">
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                setText(q.text);
                setEditing(true);
              }}
              className="rounded px-1.5 py-0.5 text-zinc-300 hover:bg-white/5"
            >
              Edit
            </button>
            {q.state === "draft" && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => void onDelete()}
                className="rounded px-1.5 py-0.5 text-rose-300 hover:bg-rose-500/10"
              >
                Delete
              </button>
            )}
          </span>
        )}
      </div>
    </li>
  );
}

function AddQuestion({ disabled, onAdd }: { disabled: boolean; onAdd: (text: string) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} disabled={disabled} className="mt-2 text-xs text-indigo-300 hover:underline">
        + Add your own question
      </button>
    );
  }
  return (
    <form
      className="mt-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim()) return;
        void onAdd(text.trim()).then((ok) => {
          if (!ok) return;
          setText("");
          setOpen(false);
        });
      }}
    >
      <textarea
        aria-label="New question"
        autoFocus
        rows={2}
        maxLength={MAX_QUESTION_CHARS}
        placeholder="A short, polite question for the client"
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={inputClass}
      />
      <div className="mt-2 flex gap-2">
        <button type="submit" disabled={disabled || !text.trim()} className={primaryButton}>
          Add question
        </button>
        <button type="button" onClick={() => setOpen(false)} className={secondaryButton}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ProposalCard({
  proposal: p,
  current,
  approved,
  disabled,
  onAccept,
  onReject,
}: {
  proposal: Proposal;
  current: string;
  approved: boolean;
  disabled: boolean;
  onAccept: (statement?: string) => Promise<boolean>;
  onReject: () => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(p.not_enough_information);
  const [text, setText] = useState(p.statement ?? current);

  return (
    <div className="rounded-lg border border-indigo-400/30 bg-indigo-500/5 p-3">
      <h4 className="text-sm font-medium text-indigo-200">Proposed update (AI suggestion)</h4>
      {p.not_enough_information ? (
        <p className="mt-2 text-sm text-zinc-300">
          Not enough information in the answers to update the statement{p.reason ? `: ${p.reason}` : "."} You can
          write the update yourself.
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm leading-relaxed text-zinc-300">
            {wordDiff(current, p.statement ?? "").map((part, index) =>
              part.kind === "added" ? (
                <ins key={index} className="rounded-sm bg-emerald-400/15 text-emerald-200 no-underline">
                  {part.text}
                </ins>
              ) : part.kind === "removed" ? (
                <del key={index} className="rounded-sm bg-rose-400/15 text-rose-200">
                  {part.text}
                </del>
              ) : (
                <span key={index}>{part.text}</span>
              ),
            )}
          </p>
          {p.reason && <p className="mt-1 text-xs text-zinc-400">{p.reason}</p>}
        </>
      )}
      {editing && (
        <textarea
          aria-label="Updated statement"
          rows={3}
          maxLength={2000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className={`${inputClass} mt-2`}
        />
      )}
      {approved && <p className="mt-2 text-xs text-amber-300">Accepting creates a new version and removes the approval.</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {editing ? (
          <button
            type="button"
            disabled={disabled || !text.trim()}
            onClick={() => void onAccept(text.trim())}
            className={primaryButton}
          >
            Save as new version
          </button>
        ) : (
          <>
            <button type="button" disabled={disabled} onClick={() => void onAccept()} className={primaryButton}>
              Accept
            </button>
            <button type="button" disabled={disabled} onClick={() => setEditing(true)} className={secondaryButton}>
              Edit & accept
            </button>
          </>
        )}
        <button type="button" disabled={disabled} onClick={() => void onReject()} className={dangerButton}>
          {p.not_enough_information ? "Dismiss" : "Reject"}
        </button>
      </div>
      <p className="mt-2 text-xs text-zinc-500">Created {formatTime(p.created_at)} · validation runs again after saving</p>
    </div>
  );
}
