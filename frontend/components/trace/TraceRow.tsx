"use client";

// One requirement of the traceability matrix (Step 10): coverage, code links, test links and its acceptance criteria.
// A requirement with no counted test link gets an amber background, so test gaps stand out.

import Link from "next/link";
import type { TraceLink, TraceRow as Row } from "@/lib/api";
import LinkChip, { CoverageBadge } from "./LinkChip";

export type LinkActions = {
  confirm: (link: TraceLink) => void;
  reject: (link: TraceLink) => void;
  add: (row: Row) => void;
};

function Links({ links, busy, actions, empty }: { links: TraceLink[]; busy: boolean; actions: LinkActions; empty: string }) {
  if (links.length === 0) return <p className="text-xs text-zinc-600">{empty}</p>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {links.map((link) => (
        <LinkChip
          key={link.id}
          link={link}
          busy={busy}
          onConfirm={() => actions.confirm(link)}
          onReject={() => actions.reject(link)}
        />
      ))}
    </ul>
  );
}

export default function TraceRow({ row, busy, actions }: { row: Row; busy: boolean; actions: LinkActions }) {
  const hasTest = row.verifies.some((l) => l.live);
  const covered = row.criteria.filter((c) => c.covered).length;
  return (
    <tr className={`align-top ${hasTest ? "" : "bg-amber-500/[0.06]"}`}>
      <td className="w-72 px-3 py-3">
        <Link
          href="/requirements"
          className="font-mono text-xs text-indigo-300 hover:underline"
        >
          {row.requirement.code}
        </Link>
        {row.requirement.module_name && <span className="ml-2 text-xs text-zinc-500">{row.requirement.module_name}</span>}
        <p className="mt-1 text-sm text-zinc-200">{row.requirement.statement}</p>
      </td>
      <td className="w-56 px-3 py-3">
        <CoverageBadge coverage={row.coverage} />
        <p className="mt-1.5 text-xs text-zinc-500">{row.coverage_reason}</p>
        {row.criteria.length > 0 && (
          <p
            className="mt-1 text-xs text-zinc-400"
            title={row.criteria
              .map((c) => `${c.code}: ${c.covered ? `tested by ${c.test_cases.join(", ")}` : "no linked test"}`)
              .join("\n")}
          >
            Criteria with a test: {covered}/{row.criteria.length}
          </p>
        )}
        {row.pending > 0 && <p className="mt-1 text-xs text-violet-300">{row.pending} AI suggestion(s) to review</p>}
      </td>
      <td className="px-3 py-3">
        <Links links={row.implements} busy={busy} actions={actions} empty="No code link" />
      </td>
      <td className="px-3 py-3">
        <Links links={row.verifies} busy={busy} actions={actions} empty="No test link" />
      </td>
      <td className="px-3 py-3 text-right">
        <button
          type="button"
          disabled={busy}
          onClick={() => actions.add(row)}
          className="whitespace-nowrap rounded px-2 py-1 text-xs text-sky-300 hover:bg-sky-500/10 disabled:opacity-50"
        >
          + Link
        </button>
      </td>
    </tr>
  );
}
