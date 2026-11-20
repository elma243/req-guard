"use client";

// One trace link (Step 10) and its coverage badge. A chip shows where the link came from (ID match, AI suggestion,
// added by a person), whether it counts, and its confidence. Clicking it shows the reason and the evidence, with
// Confirm / Reject and a link to GitHub.

import { useState } from "react";
import { COVERAGE_LABELS, TARGET_TYPE_LABELS, type Coverage, type TraceLink } from "@/lib/api";

const BADGE = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1";

export const COVERAGE_STYLES: Record<Coverage, string> = {
  not_started: "bg-white/5 text-zinc-400 ring-white/10",
  code_only: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
  tests_only: "bg-orange-400/10 text-orange-300 ring-orange-400/30",
  code_and_tests: "bg-sky-400/10 text-sky-300 ring-sky-400/30",
  verified: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
};

export function CoverageBadge({ coverage, title }: { coverage: Coverage; title?: string }) {
  return (
    <span className={`${BADGE} ${COVERAGE_STYLES[coverage]}`} title={title}>
      {COVERAGE_LABELS[coverage]}
    </span>
  );
}

const ORIGIN_LABELS: Record<TraceLink["origin"], string> = { explicit: "ID match", ai: "AI", user: "Manual" };

function chipStyle(link: TraceLink): string {
  if (link.status === "rejected") return "border-white/5 text-zinc-600 line-through";
  if (link.live) return "border-emerald-400/30 bg-emerald-400/5 text-zinc-200";
  return "border-dashed border-violet-400/40 bg-violet-400/5 text-zinc-300"; // AI suggestion waiting for a person
}

function shortLabel(link: TraceLink): string {
  if (link.target_type === "test_function") return link.target_key.split("::").at(-1) ?? link.target_label;
  if (link.target_type === "commit") return link.target_label.length > 48 ? `${link.target_label.slice(0, 48)}…` : link.target_label;
  return link.target_label;
}

export default function LinkChip({
  link,
  busy,
  onConfirm,
  onReject,
}: {
  link: TraceLink;
  busy: boolean;
  onConfirm: () => void;
  onReject: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status =
    link.status === "rejected"
      ? "Rejected"
      : link.status === "accepted"
        ? "Confirmed"
        : link.origin === "explicit"
          ? "Counts unless rejected"
          : "Waiting for a person";

  return (
    <li className={`rounded-lg border px-2 py-1 text-xs ${chipStyle(link)}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-full items-center gap-1.5 text-left"
        aria-expanded={open}
        title={link.target_label}
      >
        <span className="text-zinc-500">{TARGET_TYPE_LABELS[link.target_type]}</span>
        <span className="break-all font-mono">{shortLabel(link)}</span>
        <span className="text-zinc-500">· {ORIGIN_LABELS[link.origin]}</span>
        {link.confidence !== null && <span className="text-zinc-500">{Math.round(link.confidence * 100)}%</span>}
        {link.test_cases.length > 0 && <span className="text-sky-300">{link.test_cases.join(" ")}</span>}
        {!link.present && <span className="text-amber-300">removed</span>}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 border-t border-white/10 pt-1.5 text-zinc-400">
          <p>
            <span className="text-zinc-300">{status}.</span> {link.reason}
          </p>
          {link.evidence && <p>Found: {link.evidence}</p>}
          {link.similarity !== null && <p>Similarity {link.similarity.toFixed(2)}</p>}
          {link.ai_model && <p className="break-all">Checked by {link.ai_model}</p>}
          {link.target_detail && <p className="italic">“{link.target_detail}”</p>}
          {!link.present && <p className="text-amber-300">Not in the last analysed commit (possibly removed).</p>}
          {link.decided_by && <p>Decided by {link.decided_by}</p>}
          <div className="flex flex-wrap gap-2 pt-0.5">
            {link.status !== "accepted" && (
              <button
                type="button"
                disabled={busy}
                onClick={onConfirm}
                className="rounded px-1.5 py-0.5 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50"
              >
                {link.status === "rejected" ? "Undo reject" : "Confirm"}
              </button>
            )}
            {link.status !== "rejected" && (
              <button
                type="button"
                disabled={busy}
                onClick={onReject}
                className="rounded px-1.5 py-0.5 text-rose-300 hover:bg-rose-500/10 disabled:opacity-50"
              >
                Reject
              </button>
            )}
            <a href={link.url} target="_blank" rel="noreferrer" className="rounded px-1.5 py-0.5 text-indigo-300 hover:bg-indigo-500/10">
              Open on GitHub ↗
            </a>
          </div>
        </div>
      )}
    </li>
  );
}
