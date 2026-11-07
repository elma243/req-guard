"use client";

import {
  PAIR_CATEGORIES,
  type FindingCategory,
  type FindingSeverity,
  type Requirement,
  type RequirementDetail,
  type ValidationFinding,
} from "@/lib/api";
import { SCORE_NOTE, ScoreBadge } from "./labels";

const CRITERION_LABELS: Record<string, string> = {
  unambiguous: "Unambiguous",
  verifiable: "Measurable and verifiable",
  has_actor: "Has an actor",
  singular: "Singular (one need)",
  conforming: "Requirement wording",
  aligned: "Fits the modules and scope",
};

const CATEGORY_LABELS: Record<FindingCategory, string> = {
  vague: "Possibly vague",
  missing_info: "Possibly missing information",
  out_of_scope: "Possibly out of scope",
  module_alignment: "Module alignment",
  duplicate: "Possible duplicate",
  conflict: "Possible conflict",
  change_request: "Possible change request",
};

const SEVERITY_STYLE: Record<FindingSeverity, string> = {
  low: "text-zinc-400",
  medium: "text-amber-300",
  high: "text-rose-300",
};

// Score, failed criteria and module: from the list row, so they show at once
export function QualitySummary({ requirement: r }: { requirement: Requirement }) {
  if (r.quality_score === null) {
    return <p className="text-sm text-zinc-500">Not validated yet. Click Validate above the list.</p>;
  }
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-2 text-xs text-zinc-500">
        <ScoreBadge score={r.quality_score} />
        <span>{SCORE_NOTE}</span>
      </p>
      {r.quality_failed.length > 0 && (
        <ul className="space-y-1 text-sm text-zinc-300">
          {r.quality_failed.map((f) => (
            <li key={f.criterion}>
              <span className="text-zinc-100">{CRITERION_LABELS[f.criterion] ?? f.criterion}</span>{" "}
              <span className="text-xs tabular-nums text-zinc-500">{f.points} points</span>
              <p className="text-xs text-zinc-400">{f.reason}</p>
            </li>
          ))}
        </ul>
      )}
      {r.module_name && (
        <p className="text-xs text-zinc-400">
          Possible module: <span className="text-zinc-200">{r.module_name}</span>
        </p>
      )}
    </div>
  );
}

export function FindingList({
  detail,
  disabled = false,
  onOpen,
  onMergeInto,
}: {
  detail: RequirementDetail;
  disabled?: boolean;
  // Open the other requirement of a pair finding in the panel
  onOpen?: (id: number) => void;
  // Merge this requirement into the one it may duplicate (the Step 6 merge)
  onMergeInto?: (id: number) => void;
}) {
  const findings = detail.findings.filter((f) => f.status !== "rejected");
  if (detail.quality_score === null) return null;
  if (findings.length === 0) {
    return <p className="text-sm text-zinc-500">The checks found nothing here. A review is still needed.</p>;
  }
  const open = detail.status !== "rejected" && detail.status !== "merged";
  return (
    <ul className="space-y-2">
      {findings.map((f) => (
        <li key={f.id} className="rounded-lg border border-white/10 p-3 text-sm">
          <p className="flex items-center justify-between gap-2 text-xs">
            <span className="flex items-center gap-2 text-zinc-300">
              {CATEGORY_LABELS[f.category]}
              {f.confirmation === "unconfirmed" && (
                <span
                  title="No AI model could check this pair, so only the similarity models found it."
                  className="rounded-full px-1.5 py-px text-[11px] text-zinc-400 ring-1 ring-white/15"
                >
                  Unconfirmed
                </span>
              )}
            </span>
            <span className={`capitalize ${SEVERITY_STYLE[f.severity]}`}>{f.severity}</span>
          </p>
          <p className="mt-1 text-zinc-200">{f.reason}</p>
          {f.explanation && (
            <p className="mt-1 text-xs text-zinc-400">
              <span className="text-zinc-500">AI check: </span>
              {f.explanation}
            </p>
          )}
          {PAIR_CATEGORIES.includes(f.category) && f.related_requirement_id !== null ? (
            <PairFinding
              finding={f}
              detail={detail}
              relatedId={f.related_requirement_id}
              disabled={disabled}
              onOpen={onOpen}
              onMergeInto={open && f.category === "duplicate" ? onMergeInto : undefined}
            />
          ) : (
            <p className="mt-1 whitespace-pre-line text-xs text-zinc-500">“{f.evidence}”</p>
          )}
        </li>
      ))}
    </ul>
  );
}

// Both statements side by side, so the reader can judge the pair
function PairFinding({
  finding: f,
  detail,
  relatedId,
  disabled,
  onOpen,
  onMergeInto,
}: {
  finding: ValidationFinding;
  detail: RequirementDetail;
  relatedId: number;
  disabled: boolean;
  onOpen?: (id: number) => void;
  onMergeInto?: (id: number) => void;
}) {
  const related = f.related_code ?? "the other requirement";
  return (
    <>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <Statement label={`This (${detail.code})`} text={detail.current.statement} />
        <Statement label={related} text={f.related_statement ?? f.evidence} />
      </div>
      {(onOpen || onMergeInto) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {onMergeInto && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onMergeInto(relatedId)}
              className="rounded-md border border-white/10 px-2.5 py-1 text-xs text-zinc-300 transition hover:bg-white/5 hover:text-zinc-100 disabled:opacity-50"
            >
              Merge into {related}
            </button>
          )}
          {onOpen && (
            <button
              type="button"
              onClick={() => onOpen(relatedId)}
              className="rounded-md px-2 py-1 text-xs text-indigo-300 transition hover:bg-white/5 hover:text-indigo-200"
            >
              Open {related}
            </button>
          )}
        </div>
      )}
    </>
  );
}

function Statement({ label, text }: { label: string; text: string }) {
  return (
    <div className="min-w-0 rounded-md bg-white/5 p-2">
      <p className="font-mono text-[11px] text-zinc-500">{label}</p>
      <p className="mt-1 break-words text-zinc-200">{text}</p>
    </div>
  );
}
