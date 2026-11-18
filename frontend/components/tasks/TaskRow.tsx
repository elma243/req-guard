"use client";

// One task on the Tasks page (Step 9): its requirement and test cases, why it is in its group, the AI's assignee
// suggestion (accept, change, reject), the status, "needs review", the Jira issue, and moving it to another group.
// Nothing is assigned until the lead acts.

import Link from "next/link";
import { inputClass } from "@/components/ui";
import { TASK_STATUS_LABELS, type MemberRef, type Task, type TaskGroup, type TaskStatus } from "@/lib/api";

const BADGE = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1";
const smallButton = "rounded px-2 py-1 text-xs transition disabled:opacity-50";
const selectClass = `${inputClass} w-auto py-1 text-xs`;

const STATUS_STYLE: Record<TaskStatus, string> = {
  suggested: "bg-white/5 text-zinc-400 ring-white/10",
  assigned: "bg-sky-400/10 text-sky-300 ring-sky-400/30",
  in_progress: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
  done: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
};

export type TaskActions = {
  accept: (task: Task) => void;
  reject: (task: Task) => void;
  suggest: (task: Task) => void;
  assign: (task: Task, memberId: number) => void;
  setStatus: (task: Task, status: TaskStatus) => void;
  move: (task: Task, groupId: number) => void;
  reviewed: (task: Task) => void;
};

export default function TaskRow({
  task: t,
  members,
  groups,
  busy,
  actions,
}: {
  task: Task;
  members: MemberRef[];
  groups: TaskGroup[];
  busy: boolean;
  actions: TaskActions;
}) {
  const s = t.suggestion;
  const pending = t.assignee === null && s?.status === "suggested" && s.member !== null;
  const others = groups.filter((g) => g.id !== t.group_id);

  return (
    <li className="rounded-lg border border-white/10 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-zinc-400">{t.code}</span>
        <span className="font-mono text-xs text-zinc-300">{t.requirement.code}</span>
        <span className={`${BADGE} ${STATUS_STYLE[t.status]}`}>{TASK_STATUS_LABELS[t.status]}</span>
        {t.needs_review && (
          <span className={`${BADGE} bg-orange-400/10 text-orange-300 ring-orange-400/30`} title={t.needs_review_reason ?? ""}>
            Needs review
          </span>
        )}
        {!t.requirement.approved && (
          <span className={`${BADGE} bg-white/5 text-zinc-400 ring-white/10`}>Not approved</span>
        )}
        {t.jira_issue_key &&
          (t.jira_url ? (
            <a href={t.jira_url} target="_blank" rel="noreferrer" className="text-xs text-indigo-300 hover:underline">
              {t.jira_issue_key}
            </a>
          ) : (
            <span className="text-xs text-zinc-400">{t.jira_issue_key}</span>
          ))}
      </div>
      <p className="mt-1 text-sm text-zinc-200">{t.requirement.statement}</p>
      <p className="mt-1 text-xs text-zinc-500">
        Test cases: {t.test_cases.accepted} accepted / {t.test_cases.total}
        {t.test_cases.codes.length > 0 && ` (${t.test_cases.codes.join(", ")})`} ·{" "}
        <Link href={`/test-cases?requirement=${t.requirement.id}`} className="text-indigo-300 hover:underline">
          Open
        </Link>
      </p>
      <p className="mt-1 text-xs text-zinc-500">
        <span className="text-zinc-400">{t.group_origin === "user" ? "Placed by you: " : "Why here: "}</span>
        {t.grouping_reason}
      </p>

      {t.needs_review && (
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-orange-200/90">
          {t.needs_review_reason}. Check the task still fits the requirement.
          <button
            type="button"
            onClick={() => actions.reviewed(t)}
            disabled={busy}
            className={`${smallButton} text-orange-200 hover:bg-orange-500/10`}
          >
            Mark reviewed
          </button>
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-white/[0.02] px-2 py-2 text-xs">
        {t.assignee ? (
          <>
            <span className="text-zinc-400">
              Assigned to <span className="font-medium text-zinc-100">{t.assignee.name}</span>
            </span>
            <select
              aria-label={`Status of ${t.code}`}
              value={t.status}
              disabled={busy}
              onChange={(e) => actions.setStatus(t, e.target.value as TaskStatus)}
              className={selectClass}
            >
              <option value="assigned">Assigned</option>
              <option value="in_progress">In progress</option>
              <option value="done">Done</option>
              <option value="suggested">Unassign</option>
            </select>
          </>
        ) : pending ? (
          <>
            <span className="rounded-full bg-violet-400/10 px-2 py-0.5 text-violet-300 ring-1 ring-violet-400/30">
              AI suggestion
            </span>
            <span className="text-zinc-300">
              <span className="font-medium text-zinc-100">{s!.member!.name}</span> — {s!.reason}
              {s!.alternative && <span className="text-zinc-500"> · next best: {s!.alternative.name}</span>}
            </span>
            <span className="ml-auto flex gap-1">
              <button
                type="button"
                onClick={() => actions.accept(t)}
                disabled={busy}
                className={`${smallButton} text-emerald-300 hover:bg-emerald-500/10`}
              >
                Accept
              </button>
              <button
                type="button"
                onClick={() => actions.reject(t)}
                disabled={busy}
                className={`${smallButton} text-rose-300 hover:bg-rose-500/10`}
              >
                Reject
              </button>
            </span>
          </>
        ) : (
          <>
            <span className="text-zinc-500">
              {s?.status === "rejected" && s.member
                ? `Suggestion of ${s.member.name} rejected.`
                : s?.reason || "No suggestion yet."}
            </span>
            <button
              type="button"
              onClick={() => actions.suggest(t)}
              disabled={busy || members.length === 0}
              className={`${smallButton} text-violet-300 hover:bg-violet-500/10`}
            >
              Suggest
            </button>
          </>
        )}
        {members.length > 0 && (
          <select
            aria-label={`Assign ${t.code} to`}
            value=""
            disabled={busy}
            onChange={(e) => e.target.value && actions.assign(t, Number(e.target.value))}
            className={`${selectClass} ${t.assignee || pending ? "" : "ml-auto"}`}
          >
            <option value="">{t.assignee ? "Change assignee…" : pending ? "Pick someone else…" : "Assign to…"}</option>
            {members
              .filter((m) => m.id !== t.assignee?.id)
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
          </select>
        )}
        {others.length > 0 && (
          <select
            aria-label={`Move ${t.code} to another group`}
            value=""
            disabled={busy}
            onChange={(e) => e.target.value && actions.move(t, Number(e.target.value))}
            className={selectClass}
          >
            <option value="">Move to…</option>
            {others.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        )}
      </div>
    </li>
  );
}
