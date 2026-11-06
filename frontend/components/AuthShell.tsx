// Shared frame for the login, sign-up, verification and password pages: background, product story on the left, form card on the right.

import Link from "next/link";

export default function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="relative min-h-screen overflow-hidden bg-zinc-950 text-zinc-100">
      {/* Background: faint grid + two soft colour glows */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          maskImage: "radial-gradient(ellipse at center, black 30%, transparent 75%)",
        }}
      />
      <div aria-hidden="true" className="pointer-events-none absolute -top-40 -left-40 h-[32rem] w-[32rem] rounded-full bg-indigo-600/30 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-40 -right-32 h-[28rem] w-[28rem] rounded-full bg-amber-500/20 blur-3xl" />

      <div className="relative mx-auto grid min-h-screen max-w-6xl items-center gap-12 px-4 py-12 sm:px-8 lg:grid-cols-2">
        {/* Left: product story */}
        <section className="hidden lg:block">
          <div className="flex items-center gap-3">
            <ShieldIcon size={28} />
            <span className="text-xl font-semibold tracking-tight">ReqGuard</span>
          </div>

          <h1 className="mt-10 text-5xl font-semibold leading-[1.1] tracking-tight">
            Know what to verify{" "}
            <span className="bg-linear-to-r from-indigo-300 via-violet-300 to-amber-300 bg-clip-text text-transparent">
              before you release.
            </span>
          </h1>
          <p className="mt-5 max-w-md text-zinc-400">
            Link requirements, code and tests. Catch drift after every change, with evidence you can trace.
          </p>

          <ul className="mt-8 space-y-3 text-sm text-zinc-300">
            {[
              "Tag-free traceability recovered from your repository",
              "Two-way drift detection between requirements and code",
              "Criterion-level test evidence, with test lines quoted",
            ].map((item) => (
              <li key={item} className="flex items-center gap-3">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-300">
                  <CheckIcon />
                </span>
                {item}
              </li>
            ))}
          </ul>

          {/* Example finding card */}
          <div className="mt-10 max-w-md rounded-2xl border border-white/10 bg-white/5 p-5 shadow-2xl shadow-black/40 backdrop-blur">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-2 rounded-full bg-amber-400/10 px-2.5 py-1 text-xs font-medium text-amber-300 ring-1 ring-amber-400/30">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
                Requirement drift
              </span>
              <span className="text-xs text-zinc-500">rule-detected</span>
            </div>
            <div className="mt-4 space-y-2 font-mono text-[13px]">
              <p className="text-zinc-300">
                <span className="text-indigo-300">REQ-17</span> → reset_password()
              </p>
              <p className="text-zinc-300">
                → test_reset_password() <span className="text-rose-300">not updated</span>
              </p>
            </div>
            <div className="mt-4 flex items-center justify-between border-t border-white/10 pt-3 text-xs">
              <span className="text-zinc-400">Potential test gap · review recommended</span>
              <span className="rounded-md bg-rose-500/10 px-2 py-0.5 font-medium text-rose-300">High</span>
            </div>
          </div>
        </section>

        {/* Right: form card */}
        <section className="flex justify-center lg:justify-end">
          <div className="w-full max-w-md rounded-3xl border border-white/10 bg-zinc-900/70 p-8 shadow-2xl shadow-black/50 backdrop-blur-xl sm:p-10">
            <div className="mb-8 flex items-center gap-2 lg:hidden">
              <ShieldIcon size={28} />
              <span className="text-lg font-semibold">ReqGuard</span>
            </div>
            {children}
          </div>
        </section>
      </div>

      <p className="absolute inset-x-0 bottom-4 text-center text-xs text-zinc-600">
        © 2026 ReqGuard · Capstone Project
      </p>
    </main>
  );
}

// Text input styled for the auth card; red border when there is an error.
export function authInputClass(hasError: boolean) {
  return `w-full rounded-xl border bg-zinc-950/60 px-3.5 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/20 ${
    hasError ? "border-rose-500/70" : "border-white/10"
  }`;
}

type AuthFieldProps = React.InputHTMLAttributes<HTMLInputElement> & { id: string; label: string; error?: string };

// Labelled text input with its error message underneath.
export function AuthField({ id, label, error, ...input }: AuthFieldProps) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-zinc-300">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`mt-1.5 ${authInputClass(!!error)}`}
        {...input}
      />
      {error && (
        <p id={`${id}-error`} className="mt-1.5 text-xs text-rose-400">
          {error}
        </p>
      )}
    </div>
  );
}

// Shown in place of a form once its step is finished.
export function SuccessPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div role="status">
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300">
        <CheckIcon size={22} />
      </span>
      <h2 className="mt-5 text-2xl font-semibold tracking-tight">{title}</h2>
      <div className="mt-2 text-sm text-zinc-400">{children}</div>
    </div>
  );
}

// Full-width link styled like the submit button, for the next step after a success.
export function PrimaryLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="mt-6 flex w-full items-center justify-center rounded-xl bg-linear-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 transition hover:from-indigo-400 hover:to-violet-400"
    >
      {children}
    </Link>
  );
}

export function FormError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-xl border border-rose-500/20 bg-rose-500/10 px-3.5 py-2.5 text-sm text-rose-300">{message}</p>
  );
}

export function SubmitButton({ loading, label, loadingLabel }: { loading: boolean; label: string; loadingLabel: string }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="group flex w-full items-center justify-center gap-2 rounded-xl bg-linear-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 transition hover:from-indigo-400 hover:to-violet-400 hover:shadow-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {loading && <Spinner />}
      {loading ? loadingLabel : label}
      {!loading && <span className="transition group-hover:translate-x-0.5">→</span>}
    </button>
  );
}

// Only allow redirects to a path on this site. Blocks "//evil.com" and "https://...", and also "/\evil.com",
// because browsers read a backslash as a slash and drop tabs and line breaks.
export function safeNextPath(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !/[\\\s]/.test(next) ? next : "/projects";
}

export function ShieldIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z" fill="#F59E0B" />
      <path d="m8.5 12 2.5 2.5 4.5-5" stroke="#18181B" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CheckIcon({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m5 12 5 5 9-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}
