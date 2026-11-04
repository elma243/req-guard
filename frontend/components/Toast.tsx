"use client";

import { useCallback, useRef, useState } from "react";

const DURATION_MS = { undo: 5000, info: 4000, error: 8000 };

type Kind = keyof typeof DURATION_MS;

export type Toast = { id: number; kind: Kind; text: string; onUndo?: () => void };

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback((kind: Kind, text: string, onUndo?: () => void) => {
    const id = nextId.current++;
    setToasts((list) => [...list, { id, kind, text, onUndo }]);
  }, []);

  return { toasts, show, dismiss };
}

// Stacked, so every action in a quick series keeps its own Undo for its own time
export function ToastStack({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-6 left-1/2 z-40 flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} toast={t} onDone={() => dismiss(t.id)} />
      ))}
    </div>
  );
}

function ToastCard({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  // Hovering or focusing (reaching for Undo) stops the clock
  const [paused, setPaused] = useState(false);
  const isError = toast.kind === "error";

  return (
    <div
      role={isError ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className={`pointer-events-auto overflow-hidden rounded-xl border bg-zinc-900 text-sm shadow-xl ${
        isError ? "border-rose-500/30 text-rose-200" : "border-white/10 text-zinc-100"
      }`}
    >
      <div className="flex items-center gap-3 px-4 py-3">
        <p className="min-w-0 flex-1 break-words">{toast.text}</p>
        {toast.onUndo && (
          <button
            type="button"
            onClick={() => {
              toast.onUndo?.();
              onDone();
            }}
            className="shrink-0 rounded-md px-2 py-1 font-medium text-indigo-300 transition hover:bg-white/5 hover:text-indigo-200"
          >
            Undo
          </button>
        )}
        <button
          type="button"
          aria-label="Dismiss"
          onClick={onDone}
          className="shrink-0 rounded-md px-1.5 text-zinc-500 transition hover:text-zinc-300"
        >
          ×
        </button>
      </div>
      <div
        onAnimationEnd={onDone}
        className={`h-0.5 origin-left ${isError ? "bg-rose-400/60" : "bg-indigo-400/70"}`}
        style={{
          animation: `toast-countdown ${DURATION_MS[toast.kind]}ms linear forwards`,
          animationPlayState: paused ? "paused" : "running",
        }}
      />
    </div>
  );
}
