"use client";

import NoProjectSelected from "@/components/NoProjectSelected";
import RepositoryView from "@/components/repo/RepositoryView";
import { useSelectedProject } from "@/lib/selectedProject";

export default function RepositoryPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Repository</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        The project&apos;s GitHub Python repository: its code and tests at the last analysed commit, recent commits and
        pull requests.
      </p>
      {projectId ? <RepositoryView key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </>
  );
}
