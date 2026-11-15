// The "new answers" badge on the Clarifications menu item: client answers the lead has not opened yet.
// Polled once a minute; opening a requirement with new answers marks them seen and refreshes the count.

import { useEffect, useState } from "react";
import { getUnseenAnswers } from "@/lib/api";

const CHANGED_EVENT = "rg-answers-changed";
const POLL_MS = 60_000;

export function useUnseenAnswers(projectId: string | null): number {
  const [state, setState] = useState<{ projectId: string; count: number } | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    const load = () =>
      getUnseenAnswers(projectId)
        .then(({ count }) => {
          if (!cancelled) setState({ projectId, count });
        })
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, POLL_MS);
    window.addEventListener(CHANGED_EVENT, load);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener(CHANGED_EVENT, load);
    };
  }, [projectId]);

  return state && state.projectId === projectId ? state.count : 0;
}

// Called after answers were marked seen, so the badge does not wait for the next poll
export function answersChanged() {
  window.dispatchEvent(new Event(CHANGED_EVENT));
}
