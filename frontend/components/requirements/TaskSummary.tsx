// "Task" section of the requirement panel (Step 9): the requirement's task, its assignee and status, or how to get
// one.

import Link from "next/link";
import { TASK_STATUS_LABELS, type RequirementDetail } from "@/lib/api";

export default function TaskSummary({ detail: r }: { detail: RequirementDetail }) {
  const task = r.task;
  if (!task) {
    return (
      <p className="text-sm text-zinc-500">
        {r.approval
          ? "No task yet. Press \"Group approved requirements\" on the Tasks page."
          : "Approved requirements get a task on the Tasks page."}{" "}
        <Link href="/tasks" className="text-indigo-300 hover:underline">
          Open Tasks
        </Link>
      </p>
    );
  }
  return (
    <p className="text-sm text-zinc-300">
      <span className="font-mono">{task.code}</span> ·{" "}
      {task.assignee_name ? `${TASK_STATUS_LABELS[task.status]}, ${task.assignee_name}` : "Not assigned yet"}
      {task.needs_review && <span className="text-orange-300"> · needs review</span>} ·{" "}
      <Link href="/tasks" className="text-indigo-300 hover:underline">
        Open in Tasks
      </Link>
    </p>
  );
}
