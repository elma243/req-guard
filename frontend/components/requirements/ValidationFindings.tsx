"use client";

import type { FindingCategory, FindingSeverity, Requirement, RequirementDetail } from "@/lib/api";
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

export function FindingList({ detail }: { detail: RequirementDetail }) {
  const findings = detail.findings.filter((f) => f.status !== "rejected");
  if (detail.quality_score === null) return null;
  if (findings.length === 0) {
    return <p className="text-sm text-zinc-500">The rule checks found nothing here. A review is still needed.</p>;
  }
  return (
    <ul className="space-y-2">
      {findings.map((f) => (
        <li key={f.id} className="rounded-lg border border-white/10 p-3 text-sm">
          <p className="flex items-center justify-between gap-2 text-xs">
            <span className="text-zinc-300">{CATEGORY_LABELS[f.category]}</span>
            <span className={`capitalize ${SEVERITY_STYLE[f.severity]}`}>{f.severity}</span>
          </p>
          <p className="mt-1 text-zinc-200">{f.reason}</p>
          <p className="mt-1 whitespace-pre-line text-xs text-zinc-500">“{f.evidence}”</p>
        </li>
      ))}
    </ul>
  );
}
