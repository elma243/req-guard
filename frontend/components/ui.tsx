// Small shared styles for dashboard forms and buttons.

export const inputClass =
  "w-full rounded-lg border border-white/10 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-500/20";

export const primaryButton =
  "rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60";

export const secondaryButton =
  "rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 transition hover:bg-white/5 hover:text-zinc-100";

export const dangerButton =
  "rounded-lg border border-rose-500/30 px-4 py-2 text-sm text-rose-300 transition hover:bg-rose-500/10 disabled:opacity-60";

export function Label({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-zinc-300">
      {children}
    </label>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3.5 py-2.5 text-sm text-rose-300">{message}</p>
  );
}

// A warning that is not an error, e.g. "AI unavailable — used rule-based extraction."
export function NoticeBanner({ message, detail }: { message: string; detail?: string | null }) {
  if (!message) return null;
  return (
    <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3.5 py-2.5 text-sm text-amber-200">
      <p>{message}</p>
      {detail && <p className="mt-1 text-xs text-amber-200/70">{detail}</p>}
    </div>
  );
}

export function errorText(err: unknown) {
  return err instanceof Error ? err.message : "Something went wrong.";
}
