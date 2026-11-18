"use client";

import NoProjectSelected from "@/components/NoProjectSelected";
import TasksView from "@/components/tasks/TasksView";
import { useSelectedProject } from "@/lib/selectedProject";

export default function TasksPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Approved requirements grouped by module, one task each, with a suggested assignee and the reason. You decide
        who does what.
      </p>
      {projectId ? <TasksView key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </>
  );
}
