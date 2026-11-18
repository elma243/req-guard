"use client";

// "Push to Jira" (Step 9): lists the assigned tasks that are not in Jira yet; issues are created only when the
// lead clicks the button. Credentials stay on the backend.

import Link from "next/link";
import { useState } from "react";
import Modal from "@/components/Modal";
import { ErrorBanner, errorText, primaryButton, secondaryButton } from "@/components/ui";
import { pushTasksToJira, type JiraPushResult, type TaskBoard } from "@/lib/api";

export default function JiraPushDialog({
  projectId,
  board,
  onClose,
  onDone,
}: {
  projectId: string;
  board: TaskBoard;
  onClose: () => void;
  onDone: (result: JiraPushResult) => void;
}) {
  const candidates = board.groups.flatMap((g) => g.tasks).filter((t) => t.assignee !== null && !t.jira_issue_key);
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(candidates.map((t) => t.id)));
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const jira = board.jira;

  async function push() {
    if (sending || chosen.size === 0) return;
    setSending(true);
    setError("");
    try {
      onDone(await pushTasksToJira(projectId, [...chosen]));
    } catch (err) {
      setError(errorText(err));
      setSending(false);
    }
  }

  const toggle = (id: number) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Modal title="Push tasks to Jira" onClose={onClose} wide>
      <div className="mt-4 space-y-4 text-sm">
        {jira.missing_env.length > 0 ? (
          <ErrorBanner message={`Jira is not configured: set ${jira.missing_env.join(", ")} in backend/.env.`} />
        ) : !jira.project_key ? (
          <p className="text-zinc-300">
            Set the Jira project for tasks on the{" "}
            <Link href={`/projects/${projectId}`} className="text-indigo-300 hover:underline">
              project page
            </Link>{" "}
            first.
          </p>
        ) : candidates.length === 0 ? (
          <p className="text-zinc-400">No assigned task is waiting to be pushed. Only assigned tasks are sent to Jira.</p>
        ) : (
          <>
            <p className="text-zinc-300">
              Creates one <span className="font-medium">{jira.issue_type}</span> in Jira project{" "}
              <span className="font-mono">{jira.project_key}</span> per chosen task, with the requirement, its accepted
              criteria and test cases. The assignee is written in the description; set the Jira assignee there.
            </p>
            <ul className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-white/10 p-2">
              {candidates.map((t) => (
                <li key={t.id}>
                  <label className="flex items-start gap-2 rounded px-1 py-1 hover:bg-white/5">
                    <input
                      type="checkbox"
                      checked={chosen.has(t.id)}
                      onChange={() => toggle(t.id)}
                      className="mt-0.5 size-4 accent-indigo-500"
                    />
                    <span className="min-w-0">
                      <span className="font-mono text-xs text-zinc-400">
                        {t.code} · {t.requirement.code}
                      </span>{" "}
                      <span className="text-zinc-200">{t.requirement.statement}</span>
                      <span className="text-xs text-zinc-500"> — {t.assignee?.name}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
        <ErrorBanner message={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            Cancel
          </button>
          {jira.configured && candidates.length > 0 && (
            <button type="button" onClick={() => void push()} disabled={sending || chosen.size === 0} className={primaryButton}>
              {sending ? "Creating issues…" : `Create ${chosen.size} Jira issue${chosen.size === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
