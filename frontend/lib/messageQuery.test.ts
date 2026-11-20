// Run with: npm test (Node's built-in test runner; Node 22.18+ runs TypeScript directly)
import assert from "node:assert/strict";
import { test } from "node:test";
import { isCompleteDate, messageQuery } from "./messageQuery.ts";

test("the Messages page's default filters send no empty values", () => {
  // What the page builds with no filters set: every filter key present as ""
  const params = messageQuery({ source: "", author: "", q: "", date_from: "", date_to: "", page: 1, page_size: 50 });
  assert.equal(params.toString(), "page=1&page_size=50");
});

test("a complete date is sent", () => {
  const params = messageQuery({ date_from: "2024-03-13", date_to: "2024-03-18" });
  assert.equal(params.get("date_from"), "2024-03-13");
  assert.equal(params.get("date_to"), "2024-03-18");
});

test("half-typed or impossible dates are not sent", () => {
  for (const value of ["0002-11-20", "0202-11-20", "2024-02-30", "2024-13-01", "2024-1-5", "dd-mm-yyyy", " "]) {
    assert.equal(isCompleteDate(value), false, value);
    assert.equal(messageQuery({ date_from: value, date_to: value }).toString(), "", value);
  }
});

test("other filters are kept and trimmed", () => {
  const params = messageQuery({ source: "whatsapp", author: " sarah ", q: "delivery", date_to: "2024-02-29" });
  assert.equal(params.toString(), "source=whatsapp&author=sarah&q=delivery&date_to=2024-02-29");
});
