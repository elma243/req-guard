"use client";

import { useState } from "react";
import Modal from "@/components/Modal";
import { ErrorBanner, Label, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  editRequirement,
  type NfrCategory,
  type Requirement,
  type RequirementDetail,
  type RequirementType,
  type RequirementUpdate,
} from "@/lib/api";
import { NFR_LABELS } from "./labels";

// Saves a new version: the current text stays in the history. The client's sentences stay in view while typing.
export default function EditDialog({
  projectId,
  requirement: r,
  onSaved,
  onClose,
}: {
  projectId: string;
  requirement: Requirement;
  onSaved: (updated: RequirementDetail) => void;
  onClose: () => void;
}) {
  const v = r.current;
  const [statement, setStatement] = useState(v.statement);
  const [type, setType] = useState<RequirementType>(v.type);
  const [category, setCategory] = useState<NfrCategory>(v.nfr_category ?? "other");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const text = statement.trim();
  const changes: RequirementUpdate = {};
  if (text !== v.statement) changes.statement = text;
  if (type !== v.type) changes.type = type;
  if (type === "non_functional" && category !== v.nfr_category) changes.nfr_category = category;
  const dirty = Object.keys(changes).length > 0;

  function close() {
    if (saving) return;
    if (!dirty || confirm("Discard your changes to this requirement?")) onClose();
  }

  async function save() {
    if (!dirty || !text || saving) return;
    setSaving(true);
    setError("");
    try {
      onSaved(await editRequirement(projectId, r.id, changes));
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  }

  return (
    <Modal title={`Edit ${r.code}`} onClose={close} wide>
      <form
        className="mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {r.sources.length > 0 && (
          <ul className="max-h-32 space-y-1 overflow-y-auto border-l-2 border-white/10 pl-3 text-sm text-zinc-400">
            {r.sources.map((s) => (
              <li key={`${s.message_id}-${s.source_sentence}`} className="whitespace-pre-line">
                “{s.source_sentence}”
              </li>
            ))}
          </ul>
        )}
        <div>
          <Label htmlFor="edit-statement">Requirement (English)</Label>
          <textarea
            id="edit-statement"
            autoFocus
            required
            rows={4}
            maxLength={2000}
            value={statement}
            onChange={(e) => setStatement(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.currentTarget.form?.requestSubmit();
            }}
            className={inputClass}
          />
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <Label htmlFor="edit-type">Type</Label>
            <select
              id="edit-type"
              value={type}
              onChange={(e) => setType(e.target.value as RequirementType)}
              className={inputClass}
            >
              <option value="functional">Functional</option>
              <option value="non_functional">Non-functional</option>
            </select>
          </div>
          {type === "non_functional" && (
            <div className="flex-1">
              <Label htmlFor="edit-category">Category</Label>
              <select
                id="edit-category"
                value={category}
                onChange={(e) => setCategory(e.target.value as NfrCategory)}
                className={inputClass}
              >
                {Object.entries(NFR_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <ErrorBanner message={error} />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-zinc-500">Saved as version {v.version + 1}. The status becomes edited.</p>
          <div className="flex shrink-0 gap-2">
            <button type="button" onClick={close} className={secondaryButton}>
              Cancel
            </button>
            <button type="submit" disabled={!dirty || !text || saving} className={primaryButton}>
              {saving ? "Saving..." : "Save version"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
