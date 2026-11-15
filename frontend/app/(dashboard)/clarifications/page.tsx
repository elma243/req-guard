"use client";

import ClarificationsView from "@/components/ClarificationsView";
import NoProjectSelected from "@/components/NoProjectSelected";
import { useSelectedProject } from "@/lib/selectedProject";

export default function ClarificationsPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Clarifications</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Questions for the client about possibly vague, incomplete or conflicting requirements. Choose questions and
        create a link to share; the client answers without logging in.
      </p>
      {projectId ? <ClarificationsView key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </>
  );
}
