"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { sourceLabel } from "@/components/SourceBadge";
import { ErrorBanner, NoticeBanner, errorText, inputClass, secondaryButton } from "@/components/ui";
import {
  REQUIREMENT_STATUSES,
  RULE_BASED_MODEL,
  RULE_BASED_NOTICE,
  listRequirements,
  type Requirement,
  type RequirementStatus,
  type RequirementType,
} from "@/lib/api";
import RequirementTable, { TableSkeleton } from "./RequirementTable";

type StatusFilter = RequirementStatus | "all";

// The whole project is loaded once, so filters answer instantly and each status can show its count
function matches(r: Requirement, type: RequirementType | "", source: string, query: string) {
  if (type && r.current.type !== type) return false;
  if (source && !r.sources.some((s) => s.source === source)) return false;
  if (!query) return true;
  const text = [
    r.code,
    r.current.statement,
    r.current.actor,
    ...r.sources.flatMap((s) => [s.source_sentence, s.author]),
  ];
  return text.some((t) => t.toLocaleLowerCase().includes(query));
}

export default function RequirementsView({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<{ data?: Requirement[]; error?: string } | null>(null);
  const [reload, setReload] = useState(0);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [typeFilter, setTypeFilter] = useState<RequirementType | "">("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    listRequirements(projectId)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, reload]);

  const data = result?.data;
  const query = search.trim().toLocaleLowerCase();

  // Rows that pass every filter but status: the status chips count these
  const unfiltered = useMemo(
    () => (data ?? []).filter((r) => matches(r, typeFilter, sourceFilter, query)),
    [data, typeFilter, sourceFilter, query],
  );
  const counts = useMemo(() => {
    const byStatus: Partial<Record<RequirementStatus, number>> = {};
    for (const r of unfiltered) byStatus[r.status] = (byStatus[r.status] ?? 0) + 1;
    return byStatus;
  }, [unfiltered]);
  const visible = useMemo(
    () => (statusFilter === "all" ? unfiltered : unfiltered.filter((r) => r.status === statusFilter)),
    [unfiltered, statusFilter],
  );

  const sources = useMemo(() => [...new Set((data ?? []).flatMap((r) => r.sources.map((s) => s.source)))].sort(), [data]);
  const codes = useMemo(() => new Map((data ?? []).map((r) => [r.id, r.code])), [data]);

  const otherFilters = Boolean(typeFilter || sourceFilter || query);

  function clearFilters() {
    setStatusFilter("all");
    setTypeFilter("");
    setSourceFilter("");
    setSearch("");
  }

  function retry() {
    setResult(null); // back to the loading state
    setReload((n) => n + 1);
  }

  return (
    <div className="max-w-6xl">
      {result?.error && (
        <div className="mt-6 space-y-3">
          <ErrorBanner message={`Could not load requirements: ${result.error}`} />
          <button type="button" onClick={retry} className={secondaryButton}>
            Try again
          </button>
        </div>
      )}
      {data?.some((r) => r.current.ai_model === RULE_BASED_MODEL) && (
        <div className="mt-6">
          <NoticeBanner
            message={RULE_BASED_NOTICE}
            detail="Requirements marked “Rule-based” are the client's own sentences, found by cue words. Review them, or extract again when the AI is back."
          />
        </div>
      )}

      {data && data.length > 0 && (
        <div className="mt-6 space-y-3">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
            <StatusChip label="All" count={unfiltered.length} on={statusFilter === "all"} onClick={() => setStatusFilter("all")} />
            {REQUIREMENT_STATUSES.map((s) => (
              <StatusChip key={s} label={s} count={counts[s] ?? 0} on={statusFilter === s} onClick={() => setStatusFilter(s)} />
            ))}
          </div>
          <div className="flex flex-wrap gap-3">
            <input
              type="search"
              aria-label="Search requirements"
              placeholder="Search text, code, sentence or author"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={`${inputClass} min-w-56 flex-1`}
            />
            <select
              aria-label="Filter by type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value as RequirementType | "")}
              className={`${inputClass} w-44`}
            >
              <option value="">All types</option>
              <option value="functional">Functional</option>
              <option value="non_functional">Non-functional</option>
            </select>
            <select
              aria-label="Filter by source"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              className={`${inputClass} w-44`}
            >
              <option value="">All sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {sourceLabel(s)}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {!result && <TableSkeleton />}
      {data?.length === 0 && (
        <EmptyState title="No requirements yet">
          Import client messages, then click{" "}
          <Link href="/messages" className="text-indigo-300 hover:text-indigo-200">
            Extract requirements
          </Link>{" "}
          on the Messages page.
        </EmptyState>
      )}
      {data && data.length > 0 && visible.length === 0 && (
        statusFilter === "suggested" && !otherFilters ? (
          <EmptyState title="All reviewed">Nothing is waiting for review.</EmptyState>
        ) : (
          <EmptyState title="No requirements match these filters">
            <button type="button" onClick={clearFilters} className="text-indigo-300 hover:text-indigo-200">
              Clear filters
            </button>
          </EmptyState>
        )
      )}
      {visible.length > 0 && <RequirementTable rows={visible} codes={codes} compact={false} />}

      {data && data.length > 0 && (
        <p className="mt-4 text-sm text-zinc-500 tabular-nums">
          Showing {visible.length} of {data.length} · {data.filter((r) => r.status === "suggested").length} waiting for
          review
        </p>
      )}
    </div>
  );
}

function StatusChip({ label, count, on, onClick }: { label: string; count: number; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-sm capitalize ring-1 transition ${
        on ? "bg-indigo-500/20 text-indigo-200 ring-indigo-400/40" : "text-zinc-400 ring-white/10 hover:bg-white/5 hover:text-zinc-200"
      }`}
    >
      {label} <span className="tabular-nums text-zinc-500">{count}</span>
    </button>
  );
}

function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6 rounded-2xl border border-dashed border-white/10 p-10 text-center">
      <p className="text-sm font-medium text-zinc-200">{title}</p>
      <p className="mt-1 text-sm text-zinc-500">{children}</p>
    </div>
  );
}
