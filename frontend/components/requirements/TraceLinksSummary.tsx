"use client";

// "Trace links" section of the requirement panel (Step 10): coverage and the requirement's links to code and tests,
// with Confirm / Reject. Only approved requirements get links.

import Link from "next/link";
import { useEffect, useState } from "react";
import LinkChip, { CoverageBadge } from "@/components/trace/LinkChip";
import { errorText } from "@/components/ui";
import { confirmLink, getRequirementTrace, rejectLink, type RequirementDetail, type TraceLink, type TraceRow } from "@/lib/api";

export default function TraceLinksSummary({ projectId, detail: r }: { projectId: string; detail: RequirementDetail }) {
  const approved = Boolean(r.approval);
  const [result, setResult] = useState<{ data?: TraceRow; error?: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!approved) return;
    let cancelled = false;
    getRequirementTrace(projectId, r.id)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, r.id, approved, reload]);

  async function decide(link: TraceLink, action: "confirm" | "reject") {
    setBusy(true);
    try {
      await (action === "confirm" ? confirmLink(projectId, link.id) : rejectLink(projectId, link.id));
      setReload((n) => n + 1);
    } catch (err) {
      setResult((prev) => ({ ...prev, error: errorText(err) }));
    } finally {
      setBusy(false);
    }
  }

  if (!approved) {
    return <p className="text-sm text-zinc-500">Approved requirements get links to code and tests.</p>;
  }
  if (result?.error) return <p className="text-sm text-rose-300">{result.error}</p>;
  const row = result?.data;
  if (!row) return <p className="text-sm text-zinc-500">Loading…</p>;

  const links = [...row.implements, ...row.verifies];
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <CoverageBadge coverage={row.coverage} />
        <span className="text-xs text-zinc-400">{row.coverage_reason}</span>
      </p>
      {links.length === 0 ? (
        <p className="text-sm text-zinc-500">No links yet. Analyse the repository on the Repository page.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {links.map((link) => (
            <LinkChip
              key={link.id}
              link={link}
              busy={busy}
              onConfirm={() => void decide(link, "confirm")}
              onReject={() => void decide(link, "reject")}
            />
          ))}
        </ul>
      )}
      <Link href="/traceability" className="text-sm text-indigo-300 hover:underline">
        Open in Traceability
      </Link>
    </div>
  );
}
