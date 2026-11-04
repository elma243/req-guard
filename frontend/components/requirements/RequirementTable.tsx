"use client";

import type { Requirement } from "@/lib/api";
import { Confidence, ModelBadge, NFR_LABELS, ReviewBadge, SourceLine, StatusBadge, typeLabel } from "./labels";

type TableProps = {
  rows: Requirement[];
  // Code of each requirement, for "Merged into REQ-004"
  codes: Map<number, string>;
  // The panel beside the table is open: Type and Confidence move under the statement
  compact: boolean;
};

const HEAD = "px-4 py-3 font-medium";

export default function RequirementTable({ rows, codes, compact }: TableProps) {
  return (
    <TableShell compact={compact}>
      {rows.map((r) => (
        <Row key={r.id} requirement={r} mergedInto={r.merged_into_id ? codes.get(r.merged_into_id) : undefined} compact={compact} />
      ))}
    </TableShell>
  );
}

export function TableSkeleton() {
  return (
    <TableShell compact={false} busy>
      {[0, 1, 2, 3, 4].map((n) => (
        <tr key={n} className="animate-pulse">
          <td className="px-4 py-4">
            <div className="h-3 w-14 rounded bg-white/10" />
          </td>
          <td className="px-4 py-4">
            <div className="h-3 w-4/5 rounded bg-white/10" />
            <div className="mt-3 h-3 w-3/5 rounded bg-white/5" />
          </td>
          <td className="px-4 py-4">
            <div className="h-3 w-20 rounded bg-white/5" />
          </td>
          <td className="px-4 py-4">
            <div className="h-3 w-12 rounded bg-white/5" />
          </td>
          <td className="px-4 py-4">
            <div className="h-5 w-16 rounded-full bg-white/5" />
          </td>
        </tr>
      ))}
    </TableShell>
  );
}

function TableShell({ compact, busy, children }: { compact: boolean; busy?: boolean; children: React.ReactNode }) {
  return (
    <div className="mt-4 overflow-x-auto rounded-2xl border border-white/10" aria-busy={busy}>
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-900/80 text-xs uppercase tracking-wider text-zinc-500">
          <tr>
            <th className={`w-24 ${HEAD}`}>Code</th>
            <th className={HEAD}>Requirement and source</th>
            {!compact && <th className={`w-36 ${HEAD}`}>Type</th>}
            {!compact && <th className={`w-36 ${HEAD}`}>Confidence</th>}
            <th className={`w-32 ${HEAD}`}>Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">{children}</tbody>
      </table>
    </div>
  );
}

function Row({
  requirement: r,
  mergedInto,
  compact,
}: {
  requirement: Requirement;
  mergedInto?: string;
  compact: boolean;
}) {
  const v = r.current;
  const closed = r.status === "rejected" || r.status === "merged";
  return (
    <tr className={`align-top ${closed ? "opacity-70" : ""}`}>
      <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-zinc-300">{r.code}</td>
      <td className="px-4 py-3">
        <p className="text-zinc-100">{v.statement}</p>
        {compact && (
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <span>{typeLabel(v)}</span>
            <span className="tabular-nums">
              <Confidence value={v.confidence} />
            </span>
            <ModelBadge model={v.ai_model} />
          </p>
        )}
        {mergedInto && <p className="mt-1 text-xs text-zinc-500">Merged into {mergedInto}</p>}
        <ul className="mt-2 space-y-2">
          {r.sources.map((s) => (
            <li key={`${s.message_id}-${s.source_sentence}`} className="border-l-2 border-white/10 pl-3">
              <p className="whitespace-pre-line text-zinc-300">“{s.source_sentence}”</p>
              <div className="mt-1">
                <SourceLine source={s} />
              </div>
            </li>
          ))}
          {r.sources.length === 0 && <li className="text-xs text-zinc-600">Source message was deleted.</li>}
        </ul>
      </td>
      {!compact && (
        <td className="px-4 py-3 text-zinc-300">
          {typeLabel(v)}
          {v.nfr_category && <p className="text-xs text-zinc-500">{NFR_LABELS[v.nfr_category] ?? v.nfr_category}</p>}
        </td>
      )}
      {!compact && (
        <td className="px-4 py-3 text-zinc-300 tabular-nums">
          <Confidence value={v.confidence} />
          <div className="mt-1">
            <ModelBadge model={v.ai_model} />
          </div>
        </td>
      )}
      <td className="px-4 py-3">
        <StatusBadge status={r.status} />
        <div className="mt-1.5">
          <ReviewBadge requirement={r} />
        </div>
        {r.version_count > 1 && <p className="mt-1 text-xs text-zinc-500">v{r.version_count}</p>}
      </td>
    </tr>
  );
}
