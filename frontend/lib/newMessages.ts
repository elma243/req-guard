// The "new messages" badge on the Messages menu item: how many messages auto-sync imported since the
// Messages page was last opened. Polled once a minute; opening the Messages page clears it.

import { useEffect, useState } from "react";
import { getUnseenMessages, markMessagesSeen } from "@/lib/api";

const SEEN_EVENT = "rg-messages-seen";
const POLL_MS = 60_000;

export function useUnseenMessages(projectId: string | null): number {
  const [state, setState] = useState<{ projectId: string; count: number } | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    const load = () =>
      getUnseenMessages(projectId)
        .then(({ count }) => {
          if (!cancelled) setState({ projectId, count });
        })
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, POLL_MS);
    const onSeen = () => setState({ projectId, count: 0 });
    window.addEventListener(SEEN_EVENT, onSeen);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener(SEEN_EVENT, onSeen);
    };
  }, [projectId]);

  return state && state.projectId === projectId ? state.count : 0;
}

// Called by the Messages page when it opens
export async function markMessagesSeenNow(projectId: string) {
  await markMessagesSeen(projectId);
  window.dispatchEvent(new Event(SEEN_EVENT));
}
