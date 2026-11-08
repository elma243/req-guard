// Short relative times for status lines: "2 min ago", "in 3 min".

function span(ms: number): string {
  const minutes = Math.round(Math.abs(ms) / 60_000);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day" : `${days} days`;
}

export function timeAgo(iso: string, now: number): string {
  const ms = now - new Date(iso).getTime();
  return ms < 60_000 ? "just now" : `${span(ms)} ago`;
}

export function timeUntil(iso: string, now: number): string {
  const ms = new Date(iso).getTime() - now;
  return ms < 60_000 ? "within a minute" : `in ${span(ms)}`;
}
