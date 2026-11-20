"use client";

// Add a trace link by hand (Step 10): pick what kind of item, search the analysed repository, pick one. A link a
// person adds counts at once.

import { useEffect, useState } from "react";
import Modal from "@/components/Modal";
import { ErrorBanner, errorText, inputClass, secondaryButton } from "@/components/ui";
import { TARGET_TYPE_LABELS, addLink, searchTargets, type LinkTarget, type TargetType } from "@/lib/api";

const TYPES: TargetType[] = ["file", "test_function", "test_file", "commit", "pr"];

export default function AddLinkDialog({
  projectId,
  requirement,
  onClose,
  onAdded,
}: {
  projectId: string;
  requirement: { id: number; code: string };
  onClose: () => void;
  onAdded: () => void;
}) {
  const [type, setType] = useState<TargetType>("file");
  const [query, setQuery] = useState("");
  const [targets, setTargets] = useState<LinkTarget[] | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchTargets(projectId, query, type)
        .then((found) => !cancelled && setTargets(found))
        .catch((err) => !cancelled && setError(errorText(err)));
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [projectId, query, type]);

  async function pick(target: LinkTarget) {
    setSaving(true);
    setError("");
    try {
      await addLink(projectId, requirement.id, target.target_type, target.key);
      onAdded();
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  }

  return (
    <Modal title={`Add a link for ${requirement.code}`} onClose={onClose} wide>
      <div className="space-y-3 p-5">
        <h2 className="text-base font-semibold">Add a link for {requirement.code}</h2>
        <div className="flex flex-wrap gap-2">
          <select
            aria-label="Kind of item"
            value={type}
            onChange={(e) => setType(e.target.value as TargetType)}
            className={`${inputClass} w-auto`}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {TARGET_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
          <input
            aria-label="Search"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by path, name or title"
            maxLength={200}
            className={`${inputClass} flex-1`}
          />
        </div>
        <ErrorBanner message={error} />
        {targets === null ? (
          <p className="text-sm text-zinc-500">Searching…</p>
        ) : targets.length === 0 ? (
          <p className="text-sm text-zinc-500">Nothing found in the analysed repository.</p>
        ) : (
          <ul className="max-h-80 divide-y divide-white/5 overflow-y-auto rounded-lg border border-white/10">
            {targets.map((t) => (
              <li key={`${t.target_type}:${t.key}`} className="flex items-center gap-2 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="break-all font-mono text-xs text-zinc-200">{t.label}</p>
                  {t.detail && <p className="truncate text-xs text-zinc-500">{t.detail}</p>}
                </div>
                <a href={t.url} target="_blank" rel="noreferrer" className="text-xs text-indigo-300 hover:underline">
                  GitHub ↗
                </a>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void pick(t)}
                  className="rounded px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50"
                >
                  Link
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end">
          <button type="button" onClick={onClose} className={secondaryButton}>
            Close
          </button>
        </div>
      </div>
    </Modal>
  );
}
