// Every version of a requirement, newest first: who made it, when, and what changed from the one before.

import type { RequirementVersion } from "@/lib/api";
import { wordDiff } from "@/lib/wordDiff";
import { Confidence, ModelBadge, NFR_LABELS, formatTime, typeLabel } from "./labels";

function category(version: RequirementVersion) {
  return version.nfr_category ? (NFR_LABELS[version.nfr_category] ?? version.nfr_category) : "None";
}

function fieldChanges(before: RequirementVersion, after: RequirementVersion) {
  const changes: { label: string; from: string; to: string }[] = [];
  if (before.type !== after.type) changes.push({ label: "Type", from: typeLabel(before), to: typeLabel(after) });
  if (before.nfr_category !== after.nfr_category) {
    changes.push({ label: "Category", from: category(before), to: category(after) });
  }
  if (before.actor !== after.actor) {
    changes.push({ label: "Actor", from: before.actor || "None", to: after.actor || "None" });
  }
  return changes;
}

export default function VersionHistory({ versions }: { versions: RequirementVersion[] }) {
  return (
    <ol className="space-y-4">
      {versions
        .map((version, index) => ({ version, previous: versions[index - 1] }))
        .reverse()
        .map(({ version, previous }) => (
          <VersionEntry key={version.version} version={version} previous={previous} />
        ))}
    </ol>
  );
}

function VersionEntry({ version: v, previous }: { version: RequirementVersion; previous?: RequirementVersion }) {
  const changes = previous ? fieldChanges(previous, v) : [];
  const textChanged = previous !== undefined && previous.statement !== v.statement;

  return (
    <li className="rounded-lg border border-white/10 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
        <span className="font-mono text-zinc-200">v{v.version}</span>
        <span
          className={`rounded-full px-2 py-0.5 font-medium ring-1 ${
            v.origin === "ai"
              ? "bg-violet-400/10 text-violet-300 ring-violet-400/30"
              : "bg-sky-400/10 text-sky-300 ring-sky-400/30"
          }`}
        >
          {v.origin === "ai" ? "AI" : "User"}
        </span>
        {v.origin === "user" && <span>{v.created_by_name ?? "Deleted user"}</span>}
        {v.origin === "ai" && <ModelBadge model={v.ai_model} />}
        {v.origin === "ai" && (
          <span className="tabular-nums">
            <Confidence value={v.confidence} />
          </span>
        )}
        <span className="tabular-nums">{formatTime(v.created_at)}</span>
      </div>

      {!previous && <p className="mt-2 text-sm text-zinc-300">{v.statement}</p>}
      {textChanged && (
        <p className="mt-2 text-sm leading-relaxed text-zinc-300">
          {wordDiff(previous.statement, v.statement).map((part, index) => {
            if (part.kind === "added") {
              return (
                <ins key={index} className="rounded-sm bg-emerald-400/15 text-emerald-200 no-underline">
                  {part.text}
                </ins>
              );
            }
            if (part.kind === "removed") {
              return (
                <del key={index} className="rounded-sm bg-rose-400/15 text-rose-200">
                  {part.text}
                </del>
              );
            }
            return <span key={index}>{part.text}</span>;
          })}
        </p>
      )}
      {changes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-zinc-400">
          {changes.map((c) => (
            <li key={c.label}>
              {c.label}: {c.from} → <span className="text-zinc-200">{c.to}</span>
            </li>
          ))}
        </ul>
      )}
      {previous && !textChanged && changes.length === 0 && <p className="mt-2 text-xs text-zinc-500">No changes.</p>}
    </li>
  );
}
