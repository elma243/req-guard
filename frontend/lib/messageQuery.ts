// Query string for the Messages list. Empty filters are left out, and a date filter is sent only when it is a
// complete, real date (YYYY-MM-DD): a date box can hold a half-typed value, and the list must still load.
import type { MessageFilters } from "./api";

const DATE_KEYS = new Set(["date_from", "date_to"]);

export function isCompleteDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  if (y < 1000) return false; // a year still being typed: Chrome reports "0002-..." after the first digit
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

export function messageQuery(filters: MessageFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(filters)) {
    if (raw === undefined || raw === null) continue;
    const value = String(raw).trim();
    if (value === "") continue;
    if (DATE_KEYS.has(key) && !isCompleteDate(value)) continue;
    params.set(key, value);
  }
  return params;
}
