// Messages for the result of generating test cases, shared by the requirement panel and the Test Cases page.

import type { GenerateTestCasesResult } from "@/lib/api";

export type GenerationMessage = { kind: "info" | "error"; text: string };

export function describeGeneration(result: GenerateTestCasesResult): GenerationMessage[] {
  const messages: GenerationMessage[] = [];
  const n = result.created_test_cases;
  if (result.items.length === 0 && result.skipped.length === 0) {
    messages.push({ kind: "info", text: "Every approved requirement already has test cases that are up to date." });
  } else if (n > 0) {
    messages.push({
      kind: "info",
      text: `${n} test case${n === 1 ? "" : "s"} suggested. They are drafts until you accept them.`,
    });
  }
  for (const item of result.items) {
    if (item.not_enough_information) {
      messages.push({
        kind: "info",
        text: `Not enough information to write test cases for ${item.code}${item.reason ? `: ${item.reason}` : "."}`,
      });
    } else if (item.created_test_cases === 0 && item.reason) {
      messages.push({ kind: "info", text: `${item.code}: ${item.reason}` });
    }
  }
  if (result.error) messages.push({ kind: "error", text: `Stopped early: ${result.error}` });
  if (result.skipped.length) {
    messages.push({ kind: "info", text: `Not reached in time: ${result.skipped.join(", ")}. Run it again for the rest.` });
  }
  return messages;
}
