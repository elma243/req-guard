// Workload per team member on the Tasks page (Step 9): skills, open and done tasks, and pending suggestions.

import Link from "next/link";
import type { MemberWorkload } from "@/lib/api";

export default function WorkloadSummary({ workload, projectId }: { workload: MemberWorkload[]; projectId: string }) {
  if (workload.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 p-4 text-sm text-zinc-500">
        No team members yet. Add members with their skills on the{" "}
        <Link href={`/projects/${projectId}/team`} className="text-indigo-300 hover:underline">
          Team
        </Link>{" "}
        page to get assignee suggestions.
      </p>
    );
  }
  return (
    <section aria-label="Workload per member">
      <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Workload</h2>
      <ul className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {workload.map((w) => {
          const open = w.assigned + w.in_progress;
          return (
            <li key={w.member.id} className="rounded-xl border border-white/10 p-3">
              <p className="flex items-baseline justify-between gap-2">
                <span className="truncate text-sm font-medium text-zinc-100">{w.member.name}</span>
                <span className="text-xs text-zinc-500">{w.member.role}</span>
              </p>
              <p className="mt-1 truncate text-xs text-zinc-400" title={w.member.skills.join(", ")}>
                {w.member.skills.length ? w.member.skills.join(", ") : "No skills entered"}
              </p>
              <dl className="mt-2 grid grid-cols-4 gap-1 text-center text-xs">
                {(
                  [
                    ["Open", open],
                    ["In progress", w.in_progress],
                    ["Done", w.done],
                    ["Suggested", w.pending_suggestions],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label} className="flex flex-col-reverse rounded-lg bg-white/[0.03] px-1 py-1.5">
                    <dt className="text-[11px] text-zinc-500">{label}</dt>
                    <dd className="text-base font-semibold tabular-nums text-zinc-100">{value}</dd>
                  </div>
                ))}
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
