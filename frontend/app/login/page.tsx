"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import AuthShell, { FormError, SubmitButton, authInputClass, safeNextPath } from "@/components/AuthShell";
import { ApiError, login } from "@/lib/api";
import { emailError } from "@/lib/authRules";

// ReqGuard — Login page (http://localhost:3000/login)

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState("");
  const [needsVerifying, setNeedsVerifying] = useState(false);
  const [loading, setLoading] = useState(false);

  function validate() {
    const e: { email?: string; password?: string } = {};
    e.email = emailError(email);
    if (!password) e.password = "Password is required.";
    setErrors(e);
    return !e.email && !e.password;
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setFormError("");
    setNeedsVerifying(false);
    if (!validate()) return;

    setLoading(true);
    try {
      await login(email.trim(), password, remember);
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(safeNextPath(next));
      router.refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Something went wrong.");
      setNeedsVerifying(err instanceof ApiError && err.status === 403);
      setLoading(false);
    }
  }

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Welcome back</h2>
      <p className="mt-1.5 text-sm text-zinc-400">Sign in to your ReqGuard workspace.</p>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-zinc-300">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            aria-invalid={!!errors.email}
            className={`mt-1.5 ${authInputClass(!!errors.email)}`}
          />
          {errors.email && <p className="mt-1.5 text-xs text-rose-400">{errors.email}</p>}
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-zinc-300">
            Password
          </label>
          <div className="relative mt-1.5">
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Your password"
              aria-invalid={!!errors.password}
              className={`pr-16 ${authInputClass(!!errors.password)}`}
            />
            <button
              type="button"
              onClick={() => setShowPassword((s) => !s)}
              className="absolute inset-y-0 right-0 px-3.5 text-xs font-medium text-zinc-400 transition hover:text-zinc-100"
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          {errors.password && <p className="mt-1.5 text-xs text-rose-400">{errors.password}</p>}
        </div>

        <div className="flex items-center justify-between">
          <label className="flex cursor-pointer items-center gap-2.5 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="h-4 w-4 rounded accent-indigo-500"
            />
            Remember me
          </label>
          <Link href="/forgot-password" className="text-sm font-medium text-indigo-300 transition hover:text-indigo-200">
            Forgot password?
          </Link>
        </div>

        <FormError message={formError} />
        {needsVerifying && (
          <Link
            href={`/verify-email?email=${encodeURIComponent(email.trim())}`}
            className="block text-center text-sm font-medium text-indigo-300 transition hover:text-indigo-200"
          >
            Enter your code or get a new one
          </Link>
        )}
        <SubmitButton loading={loading} label="Sign in" loadingLabel="Signing in..." />
      </form>

      <p className="mt-8 text-center text-sm text-zinc-500">
        Don&apos;t have an account?{" "}
        <Link href="/register" className="font-medium text-indigo-300 transition hover:text-indigo-200">
          Create one
        </Link>
      </p>
    </AuthShell>
  );
}
