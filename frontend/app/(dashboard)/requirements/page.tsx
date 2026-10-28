"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import NoProjectSelected from "@/components/NoProjectSelected";
import SourceBadge from "@/components/SourceBadge";
import { ErrorBanner, errorText } from "@/components/ui";
import { listRequirements, type Requirement, type RequirementStatus } from "@/lib/api";
import { useSelectedProject } from "@/lib/selectedProject";

// Simple read-only list; the full review screen (accept / edit / reject / merge, versions) is Step 6.
export default function RequirementsPage() {
  return (
    <div className="max-w-6xl">
      <h1 className="text-2xl font-semibold tracking-tight">Requirements</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Requirements extracted from client messages, each with the client&apos;s original sentence.
      </p>
      <RequirementsForSelectedProject />
    </div>
  );
}

function RequirementsForSelectedProject() {
  const projectId = useSelectedProject();
  return projectId ? <RequirementsView key={projectId} projectId={projectId} /> : <NoProjectSelected />;
}

const STATUS_STYLE: Record<RequirementStatus, string> = {
  suggested: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
  accepted: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
  edited: "bg-sky-400/10 text-sky-300 ring-sky-400/30",
  rejected: "bg-rose-400/10 text-rose-300 ring-rose-400/30",
  merged: "bg-white/5 text-zinc-400 ring-white/10",
};

const NFR_LABELS: Record<string, string> = {
  performance: "Performance",
  security: "Security",
  usability: "Usability",
  reliability: "Reliability",
  other: "Other",
};

function RequirementsView({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<{ data?: Requirement[]; error?: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRequirements(projectId)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const data = result?.data;

  return (
    <>
      {result?.error && (
        <div className="mt-6">
          <ErrorBanner message={result.error} />
        </div>
      )}

      <div className="mt-6 overflow-x-auto rounded-2xl border border-white/10">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-900/80 text-xs uppercase tracking-wider text-zinc-500">
            <tr>
              <th className="w-24 px-4 py-3 font-medium">Code</th>
              <th className="px-4 py-3 font-medium">Requirement and source</th>
              <th className="w-40 px-4 py-3 font-medium">Type</th>
              <th className="w-36 px-4 py-3 font-medium">Confidence</th>
              <th className="w-32 px-4 py-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {!result && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">
                  Loading requirements...
                </td>
              </tr>
            )}
            {data?.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-zinc-500">
                  No requirements yet. Click{" "}
                  <Link href="/messages" className="text-indigo-300 hover:text-indigo-200">
                    Extract requirements
                  </Link>{" "}
                  on the Messages page.
                </td>
              </tr>
            )}
            {data?.map((r) => (
              <RequirementRow key={r.id} requirement={r} />
            ))}
          </tbody>
        </table>
      </div>

      {data && data.length > 0 && (
        <p className="mt-4 text-sm text-zinc-500 tabular-nums">
          {data.length} requirement{data.length === 1 ? "" : "s"} ·{" "}
          {data.filter((r) => r.status === "suggested").length} waiting for review
        </p>
      )}
    </>
  );
}

function RequirementRow({ requirement: r }: { requirement: Requirement }) {
  const v = r.current;
  return (
    <tr className="align-top">
      <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-zinc-300">{r.code}</td>
      <td className="px-4 py-3">
        <p className="text-zinc-100">{v.statement}</p>
        {v.actor && <p className="mt-0.5 text-xs text-zinc-500">Actor: {v.actor}</p>}
        <ul className="mt-2 space-y-2">
          {r.sources.map((s) => (
            <li key={`${s.message_id}-${s.source_sentence}`} className="border-l-2 border-white/10 pl-3">
              <p className="whitespace-pre-line text-zinc-300">“{s.source_sentence}”</p>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                <SourceBadge source={s.source} />
                {s.author && <span>{s.author}</span>}
                <span className="tabular-nums">
                  {new Date(s.sent_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                </span>
              </p>
            </li>
          ))}
          {r.sources.length === 0 && <li className="text-xs text-zinc-600">Source message was deleted.</li>}
        </ul>
      </td>
      <td className="px-4 py-3 text-zinc-300">
        {v.type === "functional" ? "Functional" : "Non-functional"}
        {v.nfr_category && <p className="text-xs text-zinc-500">{NFR_LABELS[v.nfr_category] ?? v.nfr_category}</p>}
      </td>
      <td className="px-4 py-3 text-zinc-300 tabular-nums">
        {v.confidence === null ? <span className="text-zinc-600">—</span> : `${Math.round(v.confidence * 100)}%`}
        {v.ai_model && <p className="mt-0.5 break-all text-xs text-zinc-500">{v.ai_model}</p>}
      </td>
      <td className="px-4 py-3">
        <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${STATUS_STYLE[r.status]}`}>
          {r.status}
        </span>
        <p className="mt-1 text-xs text-zinc-500">
          {r.origin === "ai" ? "AI" : "User"}
          {r.version_count > 1 && ` · v${r.version_count}`}
        </p>
      </td>
    </tr>
  );
}
