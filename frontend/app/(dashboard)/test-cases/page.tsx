"use client";

import { Suspense } from "react";
import NoProjectSelected from "@/components/NoProjectSelected";
import TestCasesView from "@/components/testcases/TestCasesView";
import { useSelectedProject } from "@/lib/selectedProject";

export default function TestCasesPage() {
  const projectId = useSelectedProject();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Test Cases</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Acceptance criteria and Given/When/Then test cases for approved requirements. AI suggestions are drafts until
        you accept them. They describe planned checks; they do not show that the system works.
      </p>
      {projectId ? (
        // useSearchParams (the ?requirement= filter) needs a Suspense boundary for the production build
        <Suspense fallback={<p className="mt-6 text-sm text-zinc-500">Loading…</p>}>
          <TestCasesView key={projectId} projectId={projectId} />
        </Suspense>
      ) : (
        <NoProjectSelected />
      )}
    </>
  );
}
