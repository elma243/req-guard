"use client";

import { useState } from "react";
import Modal from "@/components/Modal";
import { ErrorBanner, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import { mergeRequirements, type Requirement, type RequirementDetail } from "@/lib/api";
import { StatusBadge } from "./labels";

const sentenceKey = (s: Requirement["sources"][number]) => `${s.message_id}|${s.source_sentence}`;

export default function MergeDialog({
  projectId,
  target,
  requirements,
  onMerged,
  onClose,
}: {
  projectId: string;
  target: Requirement;
  requirements: Requirement[];
  onMerged: (updated: RequirementDetail, count: number) => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const candidates = requirements.filter((r) => r.id !== target.id && r.status !== "merged");
  const query = search.trim().toLocaleLowerCase();
  const shown = candidates.filter(
    (r) =>
      picked.has(r.id) ||
      !query ||
      r.code.toLocaleLowerCase().includes(query) ||
      r.current.statement.toLocaleLowerCase().includes(query),
  );

  // Sentences the target will gain; one it already has is kept once
  const have = new Set(target.sources.map(sentenceKey));
  const added = new Map(
    candidates
      .filter((r) => picked.has(r.id))
      .flatMap((r) => r.sources)
      .filter((s) => !have.has(sentenceKey(s)))
      .map((s) => [sentenceKey(s), s.source_sentence]),
  );

  function toggle(id: number) {
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  async function merge() {
    setSaving(true);
    setError("");
    try {
      onMerged(await mergeRequirements(projectId, target.id, [...picked]), picked.size);
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  }

  return (
    <Modal title={`Merge into ${target.code}`} onClose={() => !saving && onClose()} wide>
      <p className="mt-1 text-sm text-zinc-400">
        The ones you pick become merged and {target.code} keeps all their source messages. You can restore them later.
      </p>
      <input
        type="search"
        autoFocus
        aria-label="Search requirements to merge"
        placeholder="Search by code or text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className={`${inputClass} mt-4`}
      />
      <ul className="mt-3 max-h-64 divide-y divide-white/5 overflow-y-auto rounded-lg border border-white/10">
        {shown.map((r) => (
          <li key={r.id}>
            <label className="flex cursor-pointer items-start gap-3 px-3 py-2 text-sm hover:bg-white/[0.03]">
              <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)} className="mt-0.5" />
              <span className="font-mono text-xs leading-5 text-zinc-300">{r.code}</span>
              <span className="min-w-0 flex-1 text-zinc-200">{r.current.statement}</span>
              <StatusBadge status={r.status} />
            </label>
          </li>
        ))}
        {shown.length === 0 && (
          <li className="px-3 py-6 text-center text-sm text-zinc-500">
            {candidates.length === 0 ? "There are no other requirements to merge." : "Nothing matches your search."}
          </li>
        )}
      </ul>
      {picked.size > 0 && (
        <p className="mt-3 text-xs text-zinc-400">
          {added.size === 0
            ? `${target.code} already has their source sentences.`
            : `${added.size} source sentence${added.size === 1 ? "" : "s"} will be added to ${target.code}.`}
        </p>
      )}
      <div className="mt-3">
        <ErrorBanner message={error} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onClose} disabled={saving} className={secondaryButton}>
          Cancel
        </button>
        <button type="button" onClick={merge} disabled={picked.size === 0 || saving} className={primaryButton}>
          {saving ? "Merging..." : picked.size > 0 ? `Merge ${picked.size}` : "Merge"}
        </button>
      </div>
    </Modal>
  );
}
