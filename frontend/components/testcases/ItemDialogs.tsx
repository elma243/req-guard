"use client";

// Add or edit a test case or an acceptance criterion. Saving an edit adds a new version written by you.

import { useState } from "react";
import Modal from "@/components/Modal";
import { ErrorBanner, Label, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  TEST_CASE_TYPES,
  type AcceptanceCriterion,
  type TestCase,
  type TestCaseInput,
  type TestCaseType,
} from "@/lib/api";

export const TYPE_LABELS: Record<TestCaseType, string> = { positive: "Positive", negative: "Negative", edge: "Edge" };

const MAX_TITLE = 200;
const MAX_STEP = 1000;
const MAX_CRITERION = 500;

function useSave(onSave: () => Promise<void>) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      await onSave();
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  }
  return { saving, error, save };
}

export function TestCaseDialog({
  code,
  test,
  criteria,
  onSave,
  onClose,
}: {
  code: string; // the requirement's code
  test?: TestCase; // undefined: a new test case
  criteria: AcceptanceCriterion[];
  onSave: (data: TestCaseInput) => Promise<void>;
  onClose: () => void;
}) {
  const usable = criteria.filter((c) => c.status !== "rejected");
  const [form, setForm] = useState<TestCaseInput>({
    title: test?.title ?? "",
    type: test?.type ?? "positive",
    given: test?.given ?? "",
    when: test?.when ?? "",
    then: test?.then ?? "",
    criterion_id: test ? test.criterion_id : (usable[0]?.id ?? null),
  });
  const set = (patch: Partial<TestCaseInput>) => setForm((f) => ({ ...f, ...patch }));
  const filled = [form.title, form.given, form.when, form.then].every((v) => v.trim());
  const { saving, error, save } = useSave(() =>
    onSave({
      ...form,
      title: form.title.trim(),
      given: form.given.trim(),
      when: form.when.trim(),
      then: form.then.trim(),
    }),
  );

  return (
    <Modal title={test ? `Edit ${code} ${test.code}` : `New test case for ${code}`} onClose={onClose} wide>
      <form onSubmit={(e) => void save(e)} className="mt-4 space-y-4">
        <div>
          <Label htmlFor="tc-title">Title</Label>
          <input
            id="tc-title"
            autoFocus
            maxLength={MAX_TITLE}
            value={form.title}
            onChange={(e) => set({ title: e.target.value })}
            className={inputClass}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="tc-type">Type</Label>
            <select
              id="tc-type"
              value={form.type}
              onChange={(e) => set({ type: e.target.value as TestCaseType })}
              className={inputClass}
            >
              {TEST_CASE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="tc-criterion">Verifies</Label>
            <select
              id="tc-criterion"
              value={form.criterion_id ?? ""}
              onChange={(e) => set({ criterion_id: e.target.value ? Number(e.target.value) : null })}
              className={inputClass}
            >
              <option value="">No criterion</option>
              {usable.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code}: {c.text.length > 60 ? `${c.text.slice(0, 60)}…` : c.text}
                </option>
              ))}
            </select>
          </div>
        </div>
        {(["given", "when", "then"] as const).map((step) => (
          <div key={step}>
            <Label htmlFor={`tc-${step}`}>{step[0].toUpperCase() + step.slice(1)}</Label>
            <textarea
              id={`tc-${step}`}
              rows={2}
              maxLength={MAX_STEP}
              value={form[step]}
              onChange={(e) => set({ [step]: e.target.value })}
              className={inputClass}
            />
          </div>
        ))}
        <ErrorBanner message={error} />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-zinc-500">
            {test ? `Saved as version ${test.versions.length + 1}, written by you.` : "Saved as accepted, written by you."}
          </p>
          <div className="flex shrink-0 gap-2">
            <button type="button" onClick={onClose} className={secondaryButton}>
              Cancel
            </button>
            <button type="submit" disabled={!filled || saving} className={primaryButton}>
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export function CriterionDialog({
  code,
  criterion,
  onSave,
  onClose,
}: {
  code: string;
  criterion?: AcceptanceCriterion;
  onSave: (text: string) => Promise<void>;
  onClose: () => void;
}) {
  const [text, setText] = useState(criterion?.text ?? "");
  const { saving, error, save } = useSave(() => onSave(text.trim()));
  return (
    <Modal title={criterion ? `Edit ${code} ${criterion.code}` : `New acceptance criterion for ${code}`} onClose={onClose}>
      <form onSubmit={(e) => void save(e)} className="mt-4 space-y-4">
        <div>
          <Label htmlFor="ac-text">Criterion</Label>
          <textarea
            id="ac-text"
            autoFocus
            rows={3}
            maxLength={MAX_CRITERION}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="One testable condition, e.g. Only admins can export the order list."
            className={inputClass}
          />
        </div>
        <ErrorBanner message={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={!text.trim() || text.trim() === criterion?.text || saving}
            className={primaryButton}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
