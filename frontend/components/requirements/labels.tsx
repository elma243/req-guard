// Badges and small labels shared by the requirements table, detail panel and dialogs.

import SourceBadge from "@/components/SourceBadge";
import {
  RULE_BASED_MODEL,
  type Requirement,
  type RequirementSource,
  type RequirementStatus,
  type RequirementVersion,
} from "@/lib/api";

const STATUS_STYLE: Record<RequirementStatus, string> = {
  suggested: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
  accepted: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
  edited: "bg-sky-400/10 text-sky-300 ring-sky-400/30",
  rejected: "bg-rose-400/10 text-rose-300 ring-rose-400/30",
  merged: "bg-white/5 text-zinc-400 ring-white/10",
};

const BADGE = "whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1";

export const NFR_LABELS: Record<string, string> = {
  performance: "Performance",
  security: "Security",
  usability: "Usability",
  reliability: "Reliability",
  other: "Other",
};

export function typeLabel(version: Pick<RequirementVersion, "type">) {
  return version.type === "functional" ? "Functional" : "Non-functional";
}

export function formatTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function StatusBadge({ status }: { status: RequirementStatus }) {
  return <span className={`${BADGE} ${STATUS_STYLE[status]}`}>{status}</span>;
}

// Whether a person has confirmed this yet. A restored item whose text a user wrote gets no "AI" label.
export function ReviewBadge({ requirement: r }: { requirement: Requirement }) {
  if (r.status === "accepted" || r.status === "edited") {
    return <span className={`${BADGE} bg-emerald-400/10 text-emerald-300 ring-emerald-400/30`}>User-confirmed</span>;
  }
  if (r.status === "suggested" && r.current.origin === "ai") {
    return <span className={`${BADGE} bg-violet-400/10 text-violet-300 ring-violet-400/30`}>AI-suggested</span>;
  }
  return null;
}

export function ModelBadge({ model }: { model: string | null }) {
  if (model === RULE_BASED_MODEL) {
    return <span className={`${BADGE} bg-amber-400/10 text-amber-300 ring-amber-400/30`}>Rule-based</span>;
  }
  return model ? <span className="break-all text-xs text-zinc-500">{model}</span> : null;
}

export function Confidence({ value }: { value: number | null }) {
  return value === null ? <span className="text-zinc-600">—</span> : <>{Math.round(value * 100)}%</>;
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-white/15 bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
      {children}
    </kbd>
  );
}

export function SourceLine({ source: s }: { source: RequirementSource }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
      <SourceBadge source={s.source} />
      {s.author && <span>{s.author}</span>}
      <span className="tabular-nums">{formatTime(s.sent_at)}</span>
    </p>
  );
}
