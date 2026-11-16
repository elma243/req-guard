"use client";

// "Test cases" section of the requirement panel (Step 8B): the counts, a link to the Test Cases page, and
// "Generate test cases" for an approved requirement.

import Link from "next/link";
import { useState } from "react";
import { describeGeneration } from "@/components/testcases/generation";
import { errorText, secondaryButton } from "@/components/ui";
import { generateTestCases, getRequirement, type RequirementDetail } from "@/lib/api";

export default function TestCasesSummary({
  projectId,
  detail: r,
  disabled,
  onDetail,
  notify,
}: {
  projectId: string;
  detail: RequirementDetail;
  disabled: boolean;
  onDetail: (updated: RequirementDetail) => void;
  notify: (kind: "info" | "error", text: string) => void;
}) {
  const [working, setWorking] = useState(false);
  const approved = r.approval !== null;
  const count = r.test_case_count;

  async function generate() {
    if (working) return;
    setWorking(true);
    try {
      const result = await generateTestCases(projectId, r.id);
      for (const m of describeGeneration(result)) notify(m.kind, m.text);
      onDetail(await getRequirement(projectId, r.id));
    } catch (err) {
      notify("error", errorText(err));
    } finally {
      setWorking(false);
    }
  }

  const parts = [`${count} test case${count === 1 ? "" : "s"}`];
  if (count > 0) parts.push(`${r.accepted_test_cases} accepted`);
  if (r.test_cases_need_review > 0) parts.push(`${r.test_cases_need_review} need${r.test_cases_need_review === 1 ? "s" : ""} review`);

  return (
    <div className="space-y-3">
      <p className="text-sm text-zinc-300">
        {parts.join(" · ")}
        {(count > 0 || approved) && (
          <>
            {" · "}
            <Link href={`/test-cases?requirement=${r.id}`} className="text-indigo-300 hover:underline">
              Open in Test Cases
            </Link>
          </>
        )}
      </p>
      {r.test_cases_need_review > 0 && (
        <p className="text-xs text-orange-200/80">
          The requirement changed after some test cases were written. Check them on the Test Cases page.
        </p>
      )}
      {approved ? (
        <button type="button" onClick={() => void generate()} disabled={disabled || working} className={secondaryButton}>
          {working ? "Writing test cases…" : count > 0 ? "Generate test cases again" : "Generate test cases"}
        </button>
      ) : (
        <p className="text-xs text-zinc-500">Approve the requirement to generate test cases.</p>
      )}
    </div>
  );
}
