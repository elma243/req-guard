"use client";

import { ErrorBanner, secondaryButton } from "@/components/ui";
import type { Requirement, RequirementDetail } from "@/lib/api";
import { FindingList, QualitySummary } from "./ValidationFindings";
import VersionHistory from "./VersionHistory";
import {
  ChangeRequestBadge,
  Confidence,
  ModelBadge,
  NFR_LABELS,
  ReviewBadge,
  SourceLine,
  StatusBadge,
  typeLabel,
} from "./labels";

// Loaded detail of the open requirement: { data } or { error }; null while loading
export type DetailState = { data?: RequirementDetail; error?: string } | null;

export default function RequirementPanel({
  requirement: r,
  detail,
  codes,
  mergedHere,
  onRetry,
  onClose,
  actions,
  busy = false,
  onOpenRequirement,
  onMergeInto,
}: {
  requirement: Requirement;
  detail: DetailState;
  codes: Map<number, string>;
  // Requirements merged into this one
  mergedHere: Requirement[];
  onRetry: () => void;
  onClose: () => void;
  actions?: React.ReactNode;
  busy?: boolean;
  onOpenRequirement?: (id: number) => void;
  // Merge this requirement into another (from a possible-duplicate finding)
  onMergeInto?: (id: number) => void;
}) {
  const v = r.current;
  const mergedInto = r.merged_into_id ? codes.get(r.merged_into_id) : undefined;

  return (
    <aside
      aria-label={`Details of ${r.code}`}
      className="fixed inset-y-0 right-0 z-30 flex w-full max-w-md flex-col border-l border-white/10 bg-zinc-950 shadow-2xl"
    >
      <header className="flex items-center gap-2 border-b border-white/10 px-5 py-4">
        <span className="font-mono text-sm text-zinc-200">{r.code}</span>
        <StatusBadge status={r.status} />
        <ReviewBadge requirement={r} />
        <button
          type="button"
          aria-label="Close details"
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-0.5 text-lg leading-none text-zinc-500 transition hover:bg-white/5 hover:text-zinc-200"
        >
          ×
        </button>
      </header>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
        <section>
          <p className="text-zinc-100">{v.statement}</p>
          <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
            <span>
              {typeLabel(v)}
              {v.nfr_category && ` · ${NFR_LABELS[v.nfr_category] ?? v.nfr_category}`}
            </span>
            {v.actor && <span>Actor: {v.actor}</span>}
            <span className="tabular-nums">
              Confidence <Confidence value={v.confidence} />
            </span>
            <ModelBadge model={v.ai_model} />
          </p>
          {r.status === "rejected" && (
            <p className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {r.reject_reason ? `Rejected: ${r.reject_reason}` : "Rejected. No reason given."}
            </p>
          )}
          {r.status === "merged" && (
            <p className="mt-3 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-zinc-300">
              Merged into {mergedInto ?? "a requirement that no longer exists"}.
            </p>
          )}
          {r.change_request_for && (
            <p className="mt-3">
              <ChangeRequestBadge requirement={r} />
            </p>
          )}
          {mergedHere.length > 0 && (
            <p className="mt-3 text-xs text-zinc-400">Merged here: {mergedHere.map((m) => m.code).join(", ")}</p>
          )}
        </section>

        <Section title="Quality and findings">
          <div className="space-y-3">
            <QualitySummary requirement={r} />
            {detail?.data ? (
              <FindingList
                detail={detail.data}
                disabled={busy}
                onOpen={onOpenRequirement}
                onMergeInto={onMergeInto}
              />
            ) : (
              <Loading detail={detail} what="findings" onRetry={onRetry} />
            )}
          </div>
        </Section>

        <Section title="Source messages">
          {detail?.data ? (
            <SourceMessages detail={detail.data} codes={codes} />
          ) : (
            <Loading detail={detail} what="source messages" onRetry={onRetry} />
          )}
        </Section>

        <Section title="Version history">
          {detail?.data ? (
            <VersionHistory versions={detail.data.versions} />
          ) : (
            <Loading detail={detail} what="version history" onRetry={onRetry} />
          )}
        </Section>
      </div>

      {actions && <footer className="border-t border-white/10 px-5 py-3">{actions}</footer>}
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {children}
    </section>
  );
}

function Loading({ detail, what, onRetry }: { detail: DetailState; what: string; onRetry: () => void }) {
  if (detail?.error) {
    return (
      <div className="space-y-2">
        <ErrorBanner message={`Could not load ${what}: ${detail.error}`} />
        <button type="button" onClick={onRetry} className={secondaryButton}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <div className="animate-pulse space-y-2" aria-label={`Loading ${what}`}>
      <div className="h-3 w-full rounded bg-white/10" />
      <div className="h-3 w-3/4 rounded bg-white/5" />
    </div>
  );
}

function SourceMessages({ detail, codes }: { detail: RequirementDetail; codes: Map<number, string> }) {
  if (detail.sources.length === 0) return <p className="text-sm text-zinc-500">Source message was deleted.</p>;
  return (
    <ul className="space-y-3">
      {detail.sources.map((s) => (
        <li key={`${s.message_id}-${s.source_sentence}`} className="rounded-lg border border-white/10 p-3">
          <div className="max-h-48 overflow-y-auto">
            <HighlightedMessage text={s.message_text} sentence={s.source_sentence} />
          </div>
          <div className="mt-2">
            <SourceLine source={s} />
          </div>
          {s.merged_from_id && (
            <p className="mt-1 text-xs text-zinc-500">From {codes.get(s.merged_from_id) ?? "a merged requirement"}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function HighlightedMessage({ text, sentence }: { text: string; sentence: string }) {
  const at = text.indexOf(sentence);
  const mark = <mark className="rounded-sm bg-amber-400/20 text-amber-100">{sentence}</mark>;
  return (
    <p className="whitespace-pre-line text-sm text-zinc-300">
      {at < 0 ? (
        mark
      ) : (
        <>
          {text.slice(0, at)}
          {mark}
          {text.slice(at + sentence.length)}
        </>
      )}
    </p>
  );
}
