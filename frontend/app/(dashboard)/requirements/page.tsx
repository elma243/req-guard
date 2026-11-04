"use client";

import NoProjectSelected from "@/components/NoProjectSelected";
import RequirementsView from "@/components/requirements/RequirementsView";
import { useSelectedProject } from "@/lib/selectedProject";

export default function RequirementsPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Requirements</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Review what was extracted from client messages. Each requirement keeps the client&apos;s original sentence.
      </p>
      {projectId ? <RequirementsView key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </>
  );
}
