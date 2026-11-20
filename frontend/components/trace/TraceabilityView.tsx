"use client";

// Traceability page (Step 10): approved requirements × code × tests. Links found by the requirement ID count at
// once; AI suggestions (dashed) count only once confirmed. Rows without a counted test link are highlighted.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ToastStack, useToasts } from "@/components/Toast";
import { ErrorBanner, NoticeBanner, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  COVERAGE_LABELS,
  COVERAGE_LEVELS,
  confirmLink,
  getTrace,
  recoverLinks,
  rejectLink,
  traceExportUrl,
  type Coverage,
  type TraceFilters,
  type TraceMatrix,
  type TraceRow as Row,
} from "@/lib/api";
import AddLinkDialog from "./AddLinkDialog";
import { COVERAGE_STYLES } from "./LinkChip";
import TraceRow, { type LinkActions } from "./TraceRow";

export default function TraceabilityView({ projectId }: { projectId: string }) {
  const [filters, setFilters] = useState<TraceFilters>({ module: "", coverage: "", link_type: "", pending: false });
  const [result, setResult] = useState<{ data?: TraceMatrix; error?: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [working, setWorking] = useState<string | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const [adding, setAdding] = useState<Row | null>(null);
  const { toasts, show, dismiss } = useToasts();

  useEffect(() => {
    let cancelled = false;
    getTrace(projectId, filters)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, filters, reload]);

  async function act(label: string, action: () => Promise<void>) {
    if (working) return;
    setWorking(label);
    try {
      await action();
      setReload((n) => n + 1);
    } catch (err) {
      show("error", errorText(err));
    } finally {
      setWorking(null);
    }
  }

  const recover = () =>
    act("recover", async () => {
      const res = await recoverLinks(projectId);
      setNotices(res.notices);
      show(
        "info",
        `${res.explicit_added} ID link(s) and ${res.suggested_added} AI suggestion(s) added` +
          (res.dismissed ? `; the AI turned down ${res.dismissed}.` : "."),
      );
    });

  const actions: LinkActions = {
    confirm: (link) => void act(`link-${link.id}`, async () => void (await confirmLink(projectId, link.id))),
    reject: (link) => void act(`link-${link.id}`, async () => void (await rejectLink(projectId, link.id))),
    add: (row) => setAdding(row),
  };

  if (result?.error) {
    return (
      <div className="mt-6">
        <ErrorBanner message={`Could not load the traceability matrix: ${result.error}`} />
      </div>
    );
  }
  const matrix = result?.data;
  if (!matrix) return <p className="mt-6 text-sm text-zinc-500">Loading…</p>;

  const busy = working !== null;
  const analysed = Boolean(matrix.repo?.head_sha);
  const setCoverage = (coverage: Coverage | "") => setFilters((f) => ({ ...f, coverage }));

  return (
    <div className="mt-6 space-y-5">
      {!matrix.repo ? (
        <NoticeBanner message="No GitHub repository yet. Connect one on the project page or the Repository page, then press Analyze." />
      ) : !analysed ? (
        <NoticeBanner message={`${matrix.repo.full_name} is connected but not analysed yet. Press Analyze on the Repository page.`} />
      ) : null}

      <div className="flex flex-wrap gap-2">
        {COVERAGE_LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => setCoverage(filters.coverage === level ? "" : level)}
            className={`rounded-xl px-3 py-2 text-left text-sm ring-1 ${COVERAGE_STYLES[level]} ${
              filters.coverage === level ? "ring-2" : ""
            }`}
            aria-pressed={filters.coverage === level}
          >
            <span className="block text-lg font-semibold">{matrix.summary.counts[level]}</span>
            {COVERAGE_LABELS[level]}
          </button>
        ))}
        <div className="rounded-xl px-3 py-2 text-sm text-violet-300 ring-1 ring-violet-400/30">
          <span className="block text-lg font-semibold">{matrix.summary.pending}</span>
          AI suggestions to review
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void recover()} disabled={busy || !analysed} className={primaryButton}>
          {working === "recover" ? "Finding links…" : "Find links again"}
        </button>
        <a href={traceExportUrl(projectId)} download className={secondaryButton}>
          Export CSV
        </a>
        <select
          aria-label="Filter by module"
          value={filters.module}
          onChange={(e) => setFilters((f) => ({ ...f, module: e.target.value }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All modules</option>
          {matrix.modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by coverage"
          value={filters.coverage}
          onChange={(e) => setCoverage(e.target.value as Coverage | "")}
          className={`${inputClass} w-auto`}
        >
          <option value="">All coverage</option>
          {COVERAGE_LEVELS.map((level) => (
            <option key={level} value={level}>
              {COVERAGE_LABELS[level]}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by link type"
          value={filters.link_type}
          onChange={(e) => setFilters((f) => ({ ...f, link_type: e.target.value as TraceFilters["link_type"] }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">Any links</option>
          <option value="implements">Has code links</option>
          <option value="verifies">Has test links</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={Boolean(filters.pending)}
            onChange={(e) => setFilters((f) => ({ ...f, pending: e.target.checked }))}
            className="size-4 accent-indigo-500"
          />
          AI suggestions to review
        </label>
      </div>
      <p className="-mt-2 text-xs text-zinc-500">
        Solid links count toward coverage: the requirement ID was found next to the code, or a person confirmed or
        added the link. Dashed links are AI suggestions from similar wording; they count once you confirm them.
        Rejected links are never suggested again. Coverage shows what is linked; it does not show the release is safe.
      </p>

      {notices.map((n) => (
        <NoticeBanner key={n} message={n} />
      ))}

      {matrix.summary.total === 0 ? (
        <p className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
          No approved requirements yet. Approve requirements on the{" "}
          <Link href="/requirements" className="text-indigo-300 hover:underline">
            Requirements
          </Link>{" "}
          page; only approved requirements get trace links.
        </p>
      ) : matrix.rows.length === 0 ? (
        <p className="text-sm text-zinc-500">Nothing matches these filters.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/10">
          <table className="w-full min-w-[56rem] text-left">
            <thead className="border-b border-white/10 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-3 py-2 font-medium">Requirement</th>
                <th className="px-3 py-2 font-medium">Coverage</th>
                <th className="px-3 py-2 font-medium">Code (files, commits, PRs)</th>
                <th className="px-3 py-2 font-medium">Tests</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {matrix.rows.map((row) => (
                <TraceRow key={row.requirement.id} row={row} busy={busy} actions={actions} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {adding && (
        <AddLinkDialog
          projectId={projectId}
          requirement={adding.requirement}
          onClose={() => setAdding(null)}
          onAdded={() => {
            setAdding(null);
            show("info", `Link added to ${adding.requirement.code}.`);
            setReload((n) => n + 1);
          }}
        />
      )}
      <ToastStack toasts={toasts} dismiss={dismiss} />
    </div>
  );
}
