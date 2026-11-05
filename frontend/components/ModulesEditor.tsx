"use client";

import { inputClass, secondaryButton } from "@/components/ui";
import type { ProjectModule } from "@/lib/api";

// Limits of the backend (app/schemas/project.py)
const MAX_MODULES = 50;

// The product's parts. Rows are saved with the rest of the project form.
export default function ModulesEditor({
  modules,
  onChange,
}: {
  modules: ProjectModule[];
  onChange: (modules: ProjectModule[]) => void;
}) {
  function update(index: number, change: Partial<ProjectModule>) {
    onChange(modules.map((m, i) => (i === index ? { ...m, ...change } : m)));
  }

  return (
    <fieldset>
      <legend className="mb-1.5 block text-sm font-medium text-zinc-300">Modules</legend>
      <p className="mb-2 text-xs text-zinc-500">
        Requirements are matched to these by their words, so list the words clients use, in Bangla and Banglish too.
        After a change, click Validate on the Requirements page.
      </p>
      <ul className="space-y-2">
        {modules.map((m, i) => (
          <li key={i} className="flex items-start gap-2">
            <div className="w-1/3">
              <input
                aria-label={`Module ${i + 1} name`}
                placeholder="Name, e.g. Payments"
                maxLength={100}
                value={m.name}
                onChange={(e) => update(i, { name: e.target.value })}
                className={inputClass}
              />
            </div>
            <div className="flex-1">
              <input
                aria-label={`Module ${i + 1} description`}
                placeholder="Description, e.g. bKash, refunds, পেমেন্ট"
                maxLength={2000}
                value={m.description}
                onChange={(e) => update(i, { description: e.target.value })}
                className={inputClass}
              />
            </div>
            <button
              type="button"
              aria-label={`Remove module ${i + 1}`}
              onClick={() => onChange(modules.filter((_, row) => row !== i))}
              className="rounded-md px-2 py-2 text-lg leading-none text-zinc-500 transition hover:bg-white/5 hover:text-zinc-200"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      {modules.length < MAX_MODULES && (
        <button
          type="button"
          onClick={() => onChange([...modules, { name: "", description: "" }])}
          className={`${secondaryButton} mt-2`}
        >
          Add module
        </button>
      )}
    </fieldset>
  );
}
