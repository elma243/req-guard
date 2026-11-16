"use client";

// Test Cases page (Step 8B): acceptance criteria and Given/When/Then test cases grouped by requirement. The AI's
// items are drafts until the lead accepts them; edits are saved as new versions. Export lists approved
// requirements with their accepted items.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ToastStack, useToasts } from "@/components/Toast";
import { ErrorBanner, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import { ModelBadge, StatusBadge, formatTime } from "@/components/requirements/labels";
import {
  ITEM_STATUSES,
  TEST_CASE_TYPES,
  addCriterion,
  addTestCase,
  editCriterion,
  editTestCase,
  generateAllTestCases,
  generateTestCases,
  listTestCases,
  reviewCriterion,
  reviewTestCase,
  testCaseExportUrl,
  type AcceptanceCriterion,
  type ItemStatus,
  type TestCase,
  type TestCaseGroup,
  type TestCaseType,
  type TestCaseVersion,
} from "@/lib/api";
import { wordDiff } from "@/lib/wordDiff";
import { describeGeneration } from "./generation";
import { CriterionDialog, TYPE_LABELS, TestCaseDialog } from "./ItemDialogs";

const BADGE = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1";
const TYPE_STYLE: Record<TestCaseType, string> = {
  positive: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
  negative: "bg-rose-400/10 text-rose-300 ring-rose-400/30",
  edge: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
};
const smallButton = "rounded px-2 py-1 text-xs transition disabled:opacity-50";

type Filters = { status: ItemStatus | ""; type: TestCaseType | ""; requirement: string; needsReview: boolean };

type Dialog =
  | { kind: "test"; group: TestCaseGroup; test?: TestCase }
  | { kind: "criterion"; group: TestCaseGroup; criterion?: AcceptanceCriterion };

export default function TestCasesView({ projectId }: { projectId: string }) {
  const searchParams = useSearchParams();
  const [filters, setFilters] = useState<Filters>({
    status: "",
    type: "",
    requirement: searchParams.get("requirement") ?? "",
    needsReview: false,
  });
  const [result, setResult] = useState<{ data?: TestCaseGroup[]; error?: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [working, setWorking] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const { toasts, show, dismiss } = useToasts();

  useEffect(() => {
    let cancelled = false;
    listTestCases(projectId)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, reload]);

  const groups = result?.data;
  // Filtered here, with the same meaning as the API's filters: the type filter applies to test cases only
  const visible = useMemo(() => {
    const filtered = filters.status !== "" || filters.type !== "" || filters.needsReview;
    return (groups ?? []).flatMap((g) => {
      if (filters.requirement && String(g.requirement.id) !== filters.requirement) return [];
      const keep = (item: { status: ItemStatus; needs_review: boolean }) =>
        (!filters.status || item.status === filters.status) && (!filters.needsReview || item.needs_review);
      const criteria = filters.type ? [] : g.criteria.filter(keep);
      const tests = g.test_cases.filter((t) => keep(t) && (!filters.type || t.type === filters.type));
      if (filtered && criteria.length === 0 && tests.length === 0) return [];
      return [{ group: g, criteria, tests }];
    });
  }, [groups, filters]);

  function replaceGroup(updated: TestCaseGroup) {
    setResult((current) =>
      current?.data
        ? { data: current.data.map((g) => (g.requirement.id === updated.requirement.id ? updated : g)) }
        : current,
    );
  }

  async function act(label: string, action: () => Promise<void>) {
    if (working) return;
    setWorking(label);
    try {
      await action();
    } catch (err) {
      show("error", errorText(err));
    } finally {
      setWorking(null);
    }
  }

  const generate = (requirementId?: number) =>
    act(requirementId ? `generate-${requirementId}` : "generate", async () => {
      const res = requirementId
        ? await generateTestCases(projectId, requirementId)
        : await generateAllTestCases(projectId);
      for (const m of describeGeneration(res)) show(m.kind, m.text);
      setReload((n) => n + 1);
    });

  const review = (label: string, call: () => Promise<TestCaseGroup>) =>
    act(label, async () => replaceGroup(await call()));

  const needReview = (groups ?? []).reduce((n, g) => n + g.test_cases.filter((t) => t.needs_review).length, 0);

  if (result?.error) {
    return (
      <div className="mt-6">
        <ErrorBanner message={`Could not load test cases: ${result.error}`} />
      </div>
    );
  }
  if (!groups) return <p className="mt-6 text-sm text-zinc-500">Loading…</p>;

  return (
    <div className="mt-6 space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void generate()} disabled={working !== null} className={primaryButton}>
          {working === "generate" ? "Writing test cases…" : "Generate for all approved"}
        </button>
        <a href={testCaseExportUrl(projectId, "csv")} download className={secondaryButton}>
          Export CSV
        </a>
        <a href={testCaseExportUrl(projectId, "pdf")} download className={secondaryButton}>
          Export PDF
        </a>
        <p className="text-xs text-zinc-500">
          The export lists approved requirements with their accepted criteria and test cases.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by status"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as Filters["status"] }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All statuses</option>
          {ITEM_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s[0].toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by type"
          value={filters.type}
          onChange={(e) => setFilters((f) => ({ ...f, type: e.target.value as Filters["type"] }))}
          className={`${inputClass} w-auto`}
        >
          <option value="">All types</option>
          {TEST_CASE_TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by requirement"
          value={filters.requirement}
          onChange={(e) => setFilters((f) => ({ ...f, requirement: e.target.value }))}
          className={`${inputClass} w-auto max-w-xs`}
        >
          <option value="">All requirements</option>
          {groups.map((g) => (
            <option key={g.requirement.id} value={g.requirement.id}>
              {g.requirement.code}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={filters.needsReview}
            onChange={(e) => setFilters((f) => ({ ...f, needsReview: e.target.checked }))}
            className="size-4 accent-indigo-500"
          />
          Needs review{needReview > 0 && ` (${needReview})`}
        </label>
      </div>

      {groups.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
          No approved requirements yet. Approve requirements on the{" "}
          <Link href="/requirements" className="text-indigo-300 hover:underline">
            Requirements
          </Link>{" "}
          page, then generate their test cases here.
        </p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-zinc-500">Nothing matches these filters.</p>
      ) : (
        <ul className="space-y-5">
          {visible.map(({ group: g, criteria, tests }) => (
            <GroupCard
              key={g.requirement.id}
              group={g}
              criteria={criteria}
              tests={tests}
              working={working}
              onGenerate={() => void generate(g.requirement.id)}
              onDialog={setDialog}
              onReview={review}
              projectId={projectId}
            />
          ))}
        </ul>
      )}

      {dialog?.kind === "test" && (
        <TestCaseDialog
          code={dialog.group.requirement.code}
          test={dialog.test}
          criteria={dialog.group.criteria}
          onClose={() => setDialog(null)}
          onSave={async (data) => {
            const rid = dialog.group.requirement.id;
            replaceGroup(
              dialog.test ? await editTestCase(projectId, dialog.test.id, data) : await addTestCase(projectId, rid, data),
            );
            setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "criterion" && (
        <CriterionDialog
          code={dialog.group.requirement.code}
          criterion={dialog.criterion}
          onClose={() => setDialog(null)}
          onSave={async (text) => {
            const rid = dialog.group.requirement.id;
            replaceGroup(
              dialog.criterion
                ? await editCriterion(projectId, dialog.criterion.id, text)
                : await addCriterion(projectId, rid, text),
            );
            setDialog(null);
          }}
        />
      )}
      <ToastStack toasts={toasts} dismiss={dismiss} />
    </div>
  );
}

function GroupCard({
  projectId,
  group: g,
  criteria,
  tests,
  working,
  onGenerate,
  onDialog,
  onReview,
}: {
  projectId: string;
  group: TestCaseGroup;
  criteria: AcceptanceCriterion[];
  tests: TestCase[];
  working: string | null;
  onGenerate: () => void;
  onDialog: (dialog: Dialog) => void;
  onReview: (label: string, call: () => Promise<TestCaseGroup>) => void;
}) {
  const r = g.requirement;
  const busy = working !== null;
  const open = r.status !== "rejected" && r.status !== "merged";
  return (
    <li className="rounded-xl border border-white/10 p-4">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-zinc-200">{r.code}</span>
            <span className="text-xs text-zinc-500">v{r.version}</span>
            {r.approved ? (
              <span className={`${BADGE} bg-teal-400/10 text-teal-300 ring-teal-400/30`}>Approved</span>
            ) : (
              <span className={`${BADGE} bg-white/5 text-zinc-400 ring-white/10`}>Not approved</span>
            )}
          </p>
          <p className="mt-1 text-sm text-zinc-300">{r.statement}</p>
        </div>
        {r.approved && (
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={onGenerate} disabled={busy} className={secondaryButton}>
              {working === `generate-${r.id}` ? "Writing…" : g.test_cases.length ? "Generate again" : "Generate"}
            </button>
            <button type="button" onClick={() => onDialog({ kind: "criterion", group: g })} disabled={busy} className={secondaryButton}>
              Add criterion
            </button>
            <button type="button" onClick={() => onDialog({ kind: "test", group: g })} disabled={busy} className={secondaryButton}>
              Add test case
            </button>
          </div>
        )}
      </div>
      {!r.approved && g.test_cases.length > 0 && (
        <p className="mt-2 text-xs text-amber-200/80">
          This requirement is no longer approved. Its test cases are kept for review; approve it again to generate more.
        </p>
      )}

      {criteria.length > 0 && (
        <section className="mt-4">
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Acceptance criteria</h3>
          <ul className="mt-2 space-y-2">
            {criteria.map((c) => (
              <li key={c.id} className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2">
                <div className="flex flex-wrap items-start gap-2">
                  <span className="font-mono text-xs text-zinc-400">{c.code}</span>
                  <p className="min-w-0 flex-1 text-sm text-zinc-100">{c.text}</p>
                </div>
                <ItemFooter
                  item={c}
                  extra={<span>{c.test_case_count} test case{c.test_case_count === 1 ? "" : "s"}</span>}
                  disabled={busy || !open}
                  onAccept={() => onReview(`ac-${c.id}`, () => reviewCriterion(projectId, c.id, "accept"))}
                  onReject={() => onReview(`ac-${c.id}`, () => reviewCriterion(projectId, c.id, "reject"))}
                  onEdit={() => onDialog({ kind: "criterion", group: g, criterion: c })}
                  history={c.versions.length > 1 ? <CriterionHistory criterion={c} /> : null}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Test cases</h3>
        {tests.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-500">
            {g.test_cases.length === 0
              ? r.approved
                ? "No test cases yet. Generate them or add one by hand."
                : "No test cases."
              : "None match these filters."}
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {tests.map((t) => (
              <li key={t.id} className="rounded-lg border border-white/10 px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-zinc-400">{t.code}</span>
                  <span className="text-sm font-medium text-zinc-100">{t.title}</span>
                  <span className={`${BADGE} ${TYPE_STYLE[t.type]}`}>{TYPE_LABELS[t.type]}</span>
                </div>
                <Steps given={t.given} when={t.when} then={t.then} />
                <ItemFooter
                  item={t}
                  extra={
                    <span>
                      Verifies{" "}
                      {t.criterion_code ? (
                        <>
                          {t.criterion_code}
                          {t.criterion_status === "rejected" && " (rejected)"}
                        </>
                      ) : (
                        "no criterion"
                      )}
                    </span>
                  }
                  disabled={busy || !open}
                  onAccept={() => onReview(`tc-${t.id}`, () => reviewTestCase(projectId, t.id, "accept"))}
                  onReject={() => onReview(`tc-${t.id}`, () => reviewTestCase(projectId, t.id, "reject"))}
                  onEdit={() => onDialog({ kind: "test", group: g, test: t })}
                  history={t.versions.length > 1 ? <TestCaseHistory versions={t.versions} /> : null}
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </li>
  );
}

function Steps({ given, when, then }: { given: string; when: string; then: string }) {
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {(
        [
          ["Given", given],
          ["When", when],
          ["Then", then],
        ] as const
      ).map(([label, text]) => (
        <div key={label} className="contents">
          <dt className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</dt>
          <dd className="text-zinc-300">{text}</dd>
        </div>
      ))}
    </dl>
  );
}

function ItemFooter({
  item,
  extra,
  disabled,
  onAccept,
  onReject,
  onEdit,
  history,
}: {
  item: { origin: "ai" | "user"; status: ItemStatus; needs_review: boolean; ai_model: string | null };
  extra: React.ReactNode;
  disabled: boolean;
  onAccept: () => void;
  onReject: () => void;
  onEdit: () => void;
  history: React.ReactNode;
}) {
  const [showHistory, setShowHistory] = useState(false);
  return (
    <>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <StatusBadge status={item.status} />
        {item.needs_review && (
          <span
            className={`${BADGE} bg-orange-400/10 text-orange-300 ring-orange-400/30`}
            title="The requirement changed after this was written. Check it still fits, then accept or edit it."
          >
            Needs review
          </span>
        )}
        <span>{item.origin === "user" ? "Written by you" : item.status === "edited" ? "AI, edited" : "AI-suggested"}</span>
        {item.origin === "ai" && <ModelBadge model={item.ai_model} />}
        <span>· {extra}</span>
        <span className="ml-auto flex gap-1">
          {history && (
            <button
              type="button"
              onClick={() => setShowHistory((v) => !v)}
              className={`${smallButton} text-zinc-400 hover:bg-white/5`}
              aria-expanded={showHistory}
            >
              History
            </button>
          )}
          {(item.status !== "accepted" || item.needs_review) && (
            <button
              type="button"
              onClick={onAccept}
              disabled={disabled}
              className={`${smallButton} text-emerald-300 hover:bg-emerald-500/10`}
            >
              Accept
            </button>
          )}
          <button type="button" onClick={onEdit} disabled={disabled} className={`${smallButton} text-sky-300 hover:bg-sky-500/10`}>
            Edit
          </button>
          {item.status !== "rejected" && (
            <button
              type="button"
              onClick={onReject}
              disabled={disabled}
              className={`${smallButton} text-rose-300 hover:bg-rose-500/10`}
            >
              Reject
            </button>
          )}
        </span>
      </div>
      {showHistory && <div className="mt-2">{history}</div>}
    </>
  );
}

function Diff({ before, after }: { before: string; after: string }) {
  if (before === after) return <span>{after}</span>;
  return (
    <>
      {wordDiff(before, after).map((part, index) =>
        part.kind === "added" ? (
          <ins key={index} className="rounded-sm bg-emerald-400/15 text-emerald-200 no-underline">
            {part.text}
          </ins>
        ) : part.kind === "removed" ? (
          <del key={index} className="rounded-sm bg-rose-400/15 text-rose-200">
            {part.text}
          </del>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

function VersionHeader({ v }: { v: { version: number; origin: "ai" | "user"; created_by_name: string | null; created_at: string } }) {
  return (
    <p className="flex flex-wrap gap-2 text-xs text-zinc-500">
      <span className="font-mono text-zinc-300">v{v.version}</span>
      <span>{v.origin === "ai" ? "AI" : (v.created_by_name ?? "Deleted user")}</span>
      <span className="tabular-nums">{formatTime(v.created_at)}</span>
    </p>
  );
}

function CriterionHistory({ criterion }: { criterion: AcceptanceCriterion }) {
  const versions = criterion.versions;
  return (
    <ol className="space-y-2 border-l border-white/10 pl-3">
      {versions
        .map((v, i) => ({ v, before: versions[i - 1] }))
        .reverse()
        .map(({ v, before }) => (
          <li key={v.version}>
            <VersionHeader v={v} />
            <p className="text-sm text-zinc-300">{before ? <Diff before={before.text} after={v.text} /> : v.text}</p>
          </li>
        ))}
    </ol>
  );
}

function TestCaseHistory({ versions }: { versions: TestCaseVersion[] }) {
  return (
    <ol className="space-y-3 border-l border-white/10 pl-3">
      {versions
        .map((v, i) => ({ v, before: versions[i - 1] }))
        .reverse()
        .map(({ v, before }) => (
          <li key={v.version} className="text-sm text-zinc-300">
            <VersionHeader v={v} />
            <p className="font-medium">
              {before ? <Diff before={before.title} after={v.title} /> : v.title}
              {before && before.type !== v.type && (
                <span className="ml-2 text-xs text-zinc-500">
                  {TYPE_LABELS[before.type]} → {TYPE_LABELS[v.type]}
                </span>
              )}
            </p>
            {(["given", "when", "then"] as const).map((step) => (
              <p key={step}>
                <span className="text-xs uppercase tracking-wider text-zinc-500">{step} </span>
                {before ? <Diff before={before[step]} after={v[step]} /> : v[step]}
              </p>
            ))}
          </li>
        ))}
    </ol>
  );
}
