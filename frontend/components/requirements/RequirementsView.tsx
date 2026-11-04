"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { sourceLabel } from "@/components/SourceBadge";
import { ToastStack, useToasts } from "@/components/Toast";
import {
  ErrorBanner,
  NoticeBanner,
  dangerButton,
  errorText,
  inputClass,
  primaryButton,
  secondaryButton,
} from "@/components/ui";
import {
  BULK_LIMIT,
  REQUIREMENT_STATUSES,
  RULE_BASED_MODEL,
  RULE_BASED_NOTICE,
  acceptRequirement,
  bulkRequirements,
  getRequirement,
  listRequirements,
  rejectRequirement,
  restoreRequirement,
  type Requirement,
  type RequirementDetail,
  type RequirementStatus,
  type RequirementType,
} from "@/lib/api";
import EditDialog from "./EditDialog";
import MergeDialog from "./MergeDialog";
import RejectDialog from "./RejectDialog";
import RequirementPanel, { type DetailState } from "./RequirementPanel";
import RequirementTable, { TableSkeleton } from "./RequirementTable";

type StatusFilter = RequirementStatus | "all";

type ReviewAction = "accept" | "reject";

type Dialog = { kind: "reject"; ids: number[] } | { kind: "edit"; id: number } | { kind: "merge"; id: number };

// Moving quickly through rows only loads the one you stop on
const DETAIL_DELAY_MS = 120;

const PAST_TENSE: Record<ReviewAction, string> = { accept: "accepted", reject: "rejected" };

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

  // The row the keyboard and the panel work on
  const [activeId, setActiveId] = useState<number | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [detail, setDetail] = useState<(NonNullable<DetailState> & { id: number }) | null>(null);
  const [detailReload, setDetailReload] = useState(0);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const { toasts, show, dismiss } = useToasts();

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

  // Filtered out = no active row, so nothing can act on a requirement you cannot see
  const active = visible.find((r) => r.id === activeId) ?? null;
  const panelId = panelOpen && active ? active.id : null;
  const mergedHere = panelId === null ? [] : (data ?? []).filter((r) => r.merged_into_id === panelId);

  useEffect(() => {
    if (panelId === null) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      getRequirement(projectId, panelId)
        .then((data) => !cancelled && setDetail({ id: panelId, data }))
        .catch((err) => !cancelled && setDetail({ id: panelId, error: errorText(err) }));
    }, DETAIL_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [projectId, panelId, detailReload]);

  function open(id: number) {
    setActiveId(id);
    setPanelOpen(true);
  }

  function retryDetail() {
    setDetail(null);
    setDetailReload((n) => n + 1);
  }

  // Bulk actions work on ticked rows you can see; rows a filter hides are never touched
  const selectedRows = visible.filter((r) => selected.has(r.id));

  function setRows(updated: Requirement[]) {
    const byId = new Map(updated.map((r) => [r.id, r]));
    setResult((current) => (current?.data ? { data: current.data.map((r) => byId.get(r.id) ?? r) } : current));
  }

  function applyDetail(updated: RequirementDetail) {
    setRows([updated]);
    setDetail({ id: updated.id, data: updated });
  }

  // After a merge or restore other rows changed too (their sources)
  function refreshList() {
    listRequirements(projectId)
      .then((data) => setResult({ data }))
      .catch((err) => show("error", `Could not refresh the list: ${errorText(err)}`));
  }

  function markBusy(ids: number[], on: boolean) {
    setBusy((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  // Why this action cannot be used on this requirement; null when it can
  function blockedReason(r: Requirement, action: ReviewAction | "edit") {
    if (r.status === "merged") return `${r.code} is merged. Restore it first.`;
    if (action === "edit" && r.status === "rejected") return `${r.code} is rejected. Restore it before editing.`;
    if (action !== "edit" && r.status === PAST_TENSE[action]) return `${r.code} is already ${r.status}.`;
    return null;
  }

  // The next row becomes active, so reviewing keeps flowing. The row itself stays if it stays in the list.
  function moveOn(r: Requirement, newStatus: string) {
    if (r.id !== activeId) return;
    const index = visible.findIndex((row) => row.id === r.id);
    const leavesList = statusFilter !== "all" && statusFilter !== newStatus;
    const next = visible[index + 1] ?? (leavesList ? visible[index - 1] : r);
    setActiveId(next?.id ?? null);
  }

  async function review(r: Requirement, action: ReviewAction, reason = "") {
    const blocked = blockedReason(r, action);
    if (blocked) return show("info", blocked);
    if (busy.has(r.id)) return;
    moveOn(r, PAST_TENSE[action]);
    markBusy([r.id], true);
    try {
      applyDetail(
        action === "accept"
          ? await acceptRequirement(projectId, r.id)
          : await rejectRequirement(projectId, r.id, reason),
      );
      show("info", `${r.code} ${PAST_TENSE[action]}`);
    } catch (err) {
      show("error", `${r.code}: ${errorText(err)}`);
    } finally {
      markBusy([r.id], false);
    }
  }

  async function reviewMany(ids: number[], action: ReviewAction, reason = "") {
    if (ids.length > BULK_LIMIT) return show("error", `Select at most ${BULK_LIMIT} requirements at a time.`);
    markBusy(ids, true);
    try {
      const { updated, skipped } = await bulkRequirements(projectId, ids, action, reason);
      setRows(updated);
      setSelected(new Set());
      if (panelId !== null && updated.some((r) => r.id === panelId)) setDetailReload((n) => n + 1);
      const done = `${PAST_TENSE[action].replace(/^./, (c) => c.toUpperCase())} ${updated.length}`;
      const codes = skipped.slice(0, 3).map((s) => s.code).join(", ");
      show("info", skipped.length ? `${done}, skipped ${skipped.length}: ${codes}${skipped.length > 3 ? "..." : ""}` : done);
    } catch (err) {
      show("error", errorText(err));
    } finally {
      markBusy(ids, false);
    }
  }

  async function restore(r: Requirement) {
    if (busy.has(r.id)) return;
    markBusy([r.id], true);
    try {
      applyDetail(await restoreRequirement(projectId, r.id));
      if (r.status === "merged") refreshList();
      show("info", `${r.code} restored to suggested`);
    } catch (err) {
      show("error", `${r.code}: ${errorText(err)}`);
    } finally {
      markBusy([r.id], false);
    }
  }

  function requestReject(r: Requirement) {
    const blocked = blockedReason(r, "reject");
    if (blocked) show("info", blocked);
    else setDialog({ kind: "reject", ids: [r.id] });
  }

  function requestEdit(r: Requirement) {
    const blocked = blockedReason(r, "edit");
    if (blocked) show("info", blocked);
    else setDialog({ kind: "edit", id: r.id });
  }

  function confirmReject(ids: number[], reason: string) {
    setDialog(null);
    const single = ids.length === 1 ? data?.find((r) => r.id === ids[0]) : undefined;
    if (single) void review(single, "reject", reason);
    else void reviewMany(ids, "reject", reason);
  }

  function toggleRow(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  const dialogRequirement = dialog && dialog.kind !== "reject" ? data?.find((r) => r.id === dialog.id) : undefined;
  const rejectTitle =
    dialog?.kind === "reject"
      ? dialog.ids.length === 1
        ? `Reject ${codes.get(dialog.ids[0])}`
        : `Reject ${dialog.ids.length} requirements`
      : "";

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
    // Beside the open panel the list uses the free width instead of the usual page width
    <div className={panelId === null ? "max-w-6xl" : "lg:pr-[27.5rem]"}>
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
      {selectedRows.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-400/20 bg-indigo-500/10 px-4 py-2.5 text-sm">
          <span className="mr-2 text-indigo-100 tabular-nums">{selectedRows.length} selected</span>
          <button
            type="button"
            onClick={() => void reviewMany(selectedRows.map((r) => r.id), "accept")}
            className={primaryButton}
          >
            Accept
          </button>
          <button
            type="button"
            onClick={() => setDialog({ kind: "reject", ids: selectedRows.map((r) => r.id) })}
            className={dangerButton}
          >
            Reject
          </button>
          <button type="button" onClick={() => setSelected(new Set())} className={secondaryButton}>
            Clear
          </button>
        </div>
      )}
      {visible.length > 0 && (
        <RequirementTable
          rows={visible}
          codes={codes}
          compact={panelId !== null}
          activeId={active?.id ?? null}
          onOpen={open}
          selected={selected}
          busy={busy}
          onToggle={toggleRow}
          onToggleAll={(on) => setSelected(on ? new Set(visible.map((r) => r.id)) : new Set())}
        />
      )}

      {data && data.length > 0 && (
        <p className="mt-4 text-sm text-zinc-500 tabular-nums">
          Showing {visible.length} of {data.length} · {data.filter((r) => r.status === "suggested").length} waiting for
          review
        </p>
      )}

      {active && panelId !== null && (
        <RequirementPanel
          requirement={active}
          detail={detail?.id === panelId ? detail : null}
          codes={codes}
          mergedHere={mergedHere}
          onRetry={retryDetail}
          onClose={() => setPanelOpen(false)}
          actions={
            <PanelActions
              requirement={active}
              disabled={busy.has(active.id)}
              onAccept={() => void review(active, "accept")}
              onReject={() => requestReject(active)}
              onEdit={() => requestEdit(active)}
              onMerge={() => setDialog({ kind: "merge", id: active.id })}
              onRestore={() => void restore(active)}
            />
          }
        />
      )}

      {dialog?.kind === "reject" && (
        <RejectDialog
          title={rejectTitle}
          onConfirm={(reason) => confirmReject(dialog.ids, reason)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "edit" && dialogRequirement && (
        <EditDialog
          projectId={projectId}
          requirement={dialogRequirement}
          onSaved={(updated) => {
            applyDetail(updated);
            setDialog(null);
            show("info", `${updated.code} saved as version ${updated.current.version}`);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "merge" && dialogRequirement && (
        <MergeDialog
          projectId={projectId}
          target={dialogRequirement}
          requirements={data ?? []}
          onMerged={(updated, count) => {
            applyDetail(updated);
            refreshList();
            setDialog(null);
            show("info", `Merged ${count} into ${updated.code}`);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      <ToastStack toasts={toasts} dismiss={dismiss} />
    </div>
  );
}

function PanelActions({
  requirement: r,
  disabled,
  onAccept,
  onReject,
  onEdit,
  onMerge,
  onRestore,
}: {
  requirement: Requirement;
  disabled: boolean;
  onAccept: () => void;
  onReject: () => void;
  onEdit: () => void;
  onMerge: () => void;
  onRestore: () => void;
}) {
  const open = r.status !== "rejected" && r.status !== "merged";
  return (
    <div className="flex flex-wrap gap-2">
      {r.status !== "merged" && (
        <button type="button" onClick={onAccept} disabled={disabled || r.status === "accepted"} className={primaryButton}>
          Accept
        </button>
      )}
      {open && (
        <button type="button" onClick={onReject} disabled={disabled} className={dangerButton}>
          Reject
        </button>
      )}
      {open && (
        <button type="button" onClick={onEdit} disabled={disabled} className={secondaryButton}>
          Edit
        </button>
      )}
      {open && (
        <button type="button" onClick={onMerge} disabled={disabled} className={secondaryButton}>
          Merge
        </button>
      )}
      {!open && (
        <button type="button" onClick={onRestore} disabled={disabled} className={secondaryButton}>
          Restore to suggested
        </button>
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
