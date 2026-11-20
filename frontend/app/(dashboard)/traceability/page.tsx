"use client";

import NoProjectSelected from "@/components/NoProjectSelected";
import TraceabilityView from "@/components/trace/TraceabilityView";
import { useSelectedProject } from "@/lib/selectedProject";

export default function TraceabilityPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Traceability</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Approved requirements linked to the code and tests of the GitHub repository, with how far each one is covered.
        You confirm or reject what the AI suggests.
      </p>
      {projectId ? <TraceabilityView key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </>
  );
}
