"use client";

// Tasks page (Step 9): approved requirements grouped into task groups (by module, or similar requirements the AI
// named), one task each, with an AI-suggested assignee and its reason. Nothing is assigned until the lead accepts
// or picks someone. Export as CSV; "Push to Jira" creates issues only from its dialog.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ToastStack, useToasts } from "@/components/Toast";
import { ErrorBanner, NoticeBanner, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import { ModelBadge } from "@/components/requirements/labels";
import {
  TASK_STATUSES,
  TASK_STATUS_LABELS,
  acceptAllSuggestions,
  acceptSuggestion,
  assignTask,
  groupRequirements,
  listTasks,
  markTaskReviewed,
  rejectSuggestion,
  renameTaskGroup,
  suggestAgain,
  suggestAssignees,
  taskExportUrl,
  updateTask,
  type JiraPushResult,
  type TaskBoard,
  type TaskFilters,
  type TaskGroup,
} from "@/lib/api";
import JiraPushDialog from "./JiraPushDialog";
import TaskRow, { type TaskActions } from "./TaskRow";
import WorkloadSummary from "./WorkloadSummary";

const BADGE = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1";
const KIND_LABELS: Record<TaskGroup["kind"], { label: string; style: string }> = {
  module: { label: "Module", style: "bg-teal-400/10 text-teal-300 ring-teal-400/30" },
  cluster: { label: "Similar requirements", style: "bg-violet-400/10 text-violet-300 ring-violet-400/30" },
  unmatched: { label: "Other", style: "bg-white/5 text-zinc-400 ring-white/10" },
};

export default function TasksView({ projectId }: { projectId: string }) {
  const [filters, setFilters] = useState<TaskFilters>({ member_id: "", status: "", module: "", needs_review: false });
  const [result, setResult] = useState<{ data?: TaskBoard; error?: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [working, setWorking] = useState<string | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const [pushing, setPushing] = useState(false);
  const { toasts, show, dismiss } = useToasts();

  useEffect(() => {
    let cancelled = false;
    listTasks(projectId, filters)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, filters, reload]);

  async function act(label: string, action: () => Promise<void>) {
    if (working) return;
    setWorking(label);
    try {
      await action();
      setReload((n) => n + 1);
    } catch (err) {
      show("error", errorText(err));
    } finally {
      setWorking(null);
    }
  }

  const group = () =>
    act("group", async () => {
      const res = await groupRequirements(projectId);
      setNotices(res.notices);
      if (res.created_tasks > 0) {
        const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
        show(
          "info",
          `${plural(res.created_tasks, "new task")}, ${plural(res.created_groups, "new group")}, ` +
            `${plural(res.suggested, "assignee suggestion")}.`,
        );
      }
    });

  const suggestAll = () =>
    act("suggest", async () => {
      const res = await suggestAssignees(projectId);
      setNotices(res.notices);
      show("info", `${res.suggested} suggestion${res.suggested === 1 ? "" : "s"} made.`);
    });

  const acceptAll = () =>
    act("accept-all", async () => {
      const res = await acceptAllSuggestions(projectId);
      const skipped = res.skipped_needs_review
        ? ` ${res.skipped_needs_review} skipped because they need review first.`
        : "";
      show("info", `${res.accepted} suggestion${res.accepted === 1 ? "" : "s"} accepted.${skipped}`);
    });

  const actions: TaskActions = {
    accept: (t) => void act(`task-${t.id}`, async () => void (await acceptSuggestion(projectId, t.id))),
    reject: (t) => void act(`task-${t.id}`, async () => void (await rejectSuggestion(projectId, t.id))),
    suggest: (t) => void act(`task-${t.id}`, async () => void (await suggestAgain(projectId, t.id))),
    assign: (t, memberId) => void act(`task-${t.id}`, async () => void (await assignTask(projectId, t.id, memberId))),
    setStatus: (t, status) => void act(`task-${t.id}`, async () => void (await updateTask(projectId, t.id, { status }))),
    move: (t, groupId) => void act(`task-${t.id}`, async () => void (await updateTask(projectId, t.id, { group_id: groupId }))),
    reviewed: (t) => void act(`task-${t.id}`, async () => void (await markTaskReviewed(projectId, t.id))),
  };

  function pushed(res: JiraPushResult) {
    setPushing(false);
    if (res.pushed.length) {
      show("info", `Created ${res.pushed.map((p) => `${p.key} (${p.code})`).join(", ")} in Jira.`);
    }
    if (res.error) show("error", `Jira: ${res.error}`);
    else if (!res.pushed.length) show("info", "Nothing was sent to Jira.");
    setReload((n) => n + 1);
  }

  if (result?.error) {
    return (
      <div className="mt-6">
        <ErrorBanner message={`Could not load tasks: ${result.error}`} />
      </div>
    );
  }
  const board = result?.data;
  if (!board) return <p className="mt-6 text-sm text-zinc-500">Loading…</p>;

  const busy = working !== null;
  const members = board.workload.map((w) => w.member);
  const allTasks = board.groups.flatMap((g) => g.tasks);
  const pending = allTasks.filter((t) => t.assignee === null && t.suggestion?.status === "suggested").length;
  const filtered = Boolean(filters.member_id || filters.status || filters.module || filters.needs_review);

  return (
    <div className="mt-6 space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void group()} disabled={busy} className={primaryButton}>
          {working === "group" ? "Grouping…" : "Group approved requirements"}
        </button>
        <button type="button" onClick={() => void suggestAll()} disabled={busy} className={secondaryButton}>
          {working === "suggest" ? "Suggesting…" : "Suggest assignees"}
        </button>
        <button type="button" onClick={() => void acceptAll()} disabled={busy || pending === 0} className={secondaryButton}>
          {working === "accept-all" ? "Accepting…" : `Accept all suggestions${pending ? ` (${pending})` : ""}`}
        </button>
        <a href={taskExportUrl(projectId)} download className={secondaryButton}>
          Export CSV
        </a>
        <button type="button" onClick={() => setPushing(true)} disabled={busy} className={secondaryButton}>
          Push to Jira…
        </button>
      </div>
      <p className="-mt-3 text-xs text-zinc-500">
        Suggestions come from matching task words to member skills and from current workload. They are only
        suggestions: a task is assigned when you accept one or pick someone. Tasks that need review are left out of
        &quot;Accept all&quot;.
      </p>

      {notices.map((n) => (
        <NoticeBanner key={n} message={n} />
      ))}

      <WorkloadSummary workload={board.workload} projectId={projectId} />

      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by member"
          value={filters.member_id}
          onChange={(e) => setFilters((f) => ({ ...f, member_id: e.target.value }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All members</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by status"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as TaskFilters["status"] }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All statuses</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {TASK_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by module"
          value={filters.module}
          onChange={(e) => setFilters((f) => ({ ...f, module: e.target.value }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All modules</option>
          {board.modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={Boolean(filters.needs_review)}
            onChange={(e) => setFilters((f) => ({ ...f, needs_review: e.target.checked }))}
            className="size-4 accent-indigo-500"
          />
          Needs review
        </label>
      </div>

      {board.total_tasks === 0 ? (
        <p className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
          No tasks yet. Approve requirements on the{" "}
          <Link href="/requirements" className="text-indigo-300 hover:underline">
            Requirements
          </Link>{" "}
          page, then press &quot;Group approved requirements&quot;.
        </p>
      ) : board.groups.length === 0 && filtered ? (
        <p className="text-sm text-zinc-500">Nothing matches these filters.</p>
      ) : (
        <ul className="space-y-5">
          {board.groups.map((g) => (
            <GroupCard
              key={g.id}
              group={g}
              groups={board.groups}
              members={members}
              busy={busy}
              actions={actions}
              onRename={(name) => act(`group-${g.id}`, async () => void (await renameTaskGroup(projectId, g.id, name)))}
            />
          ))}
        </ul>
      )}

      {pushing && (
        <JiraPushDialog projectId={projectId} board={board} onClose={() => setPushing(false)} onDone={pushed} />
      )}
      <ToastStack toasts={toasts} dismiss={dismiss} />
    </div>
  );
}

function GroupCard({
  group: g,
  groups,
  members,
  busy,
  actions,
  onRename,
}: {
  group: TaskGroup;
  groups: TaskGroup[];
  members: { id: number; name: string }[];
  busy: boolean;
  actions: TaskActions;
  onRename: (name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(g.name);
  const kind = KIND_LABELS[g.kind];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed && trimmed !== g.name) await onRename(trimmed);
    setEditing(false);
  }

  return (
    <li className="rounded-xl border border-white/10 p-4">
      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <form onSubmit={(e) => void save(e)} className="flex items-center gap-2">
            <input
              aria-label="Group name"
              autoFocus
              maxLength={60}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={`${inputClass} w-64 py-1`}
            />
            <button type="submit" disabled={busy || !name.trim()} className="rounded px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/10">
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setName(g.name);
                setEditing(false);
              }}
              className="rounded px-2 py-1 text-xs text-zinc-400 hover:bg-white/5"
            >
              Cancel
            </button>
          </form>
        ) : (
          <>
            <h2 className="text-base font-semibold text-zinc-100">{g.name}</h2>
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={busy}
              className="rounded px-1.5 py-0.5 text-xs text-sky-300 hover:bg-sky-500/10"
            >
              Rename
            </button>
          </>
        )}
        <span className={`${BADGE} ${kind.style}`}>{kind.label}</span>
        {g.kind === "cluster" &&
          (g.origin === "user" ? (
            <span className="text-xs text-zinc-500">Named by you</span>
          ) : g.ai_model === "rule-based" ? (
            <ModelBadge model={g.ai_model} />
          ) : (
            <span className="flex items-center gap-1 text-xs text-zinc-500">
              AI-named <ModelBadge model={g.ai_model} />
            </span>
          ))}
        <span className="ml-auto text-xs text-zinc-500">
          {g.tasks.length} task{g.tasks.length === 1 ? "" : "s"}
        </span>
      </div>
      {g.reason && <p className="mt-1 text-xs text-zinc-400">{g.reason}</p>}
      <ul className="mt-3 space-y-2">
        {g.tasks.map((t) => (
          <TaskRow key={t.id} task={t} members={members} groups={groups} busy={busy} actions={actions} />
        ))}
      </ul>
    </li>
  );
}
