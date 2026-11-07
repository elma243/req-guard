"use client";

import Link from "next/link";
import { useState } from "react";
import AuthShell, { AuthField, FormError, SubmitButton, SuccessPanel } from "@/components/AuthShell";
import { ApiError, forgotPassword } from "@/lib/api";
import { emailError } from "@/lib/authRules";

// ReqGuard — Forgot password page (http://localhost:3000/forgot-password)

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState("");
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sentMessage, setSentMessage] = useState("");

  const problem = emailError(email);

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setFormError("");
    setTouched(true);
    if (problem) return;

    setLoading(true);
    try {
      const { message } = await forgotPassword(email.trim());
      setSentMessage(message);
    } catch (err) {
      if (err instanceof ApiError && err.fields.email) setServerError(err.fields.email);
      else setFormError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  if (sentMessage) {
    return (
      <AuthShell>
        <SuccessPanel title="Check your email">
          {sentMessage}. The link works once and expires in 30 minutes.
        </SuccessPanel>
        <p className="mt-6 text-sm">
          <Link href="/login" className="font-medium text-indigo-300 transition hover:text-indigo-200">
            Back to sign in
          </Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Forgot your password?</h2>
      <p className="mt-1.5 text-sm text-zinc-400">Enter your email and we will send you a link to reset it.</p>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        <AuthField
          id="email"
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setServerError("");
          }}
          onBlur={() => setTouched(true)}
          error={serverError || (touched ? problem : undefined)}
        />

        <FormError message={formError} />
        <SubmitButton loading={loading} label="Send reset link" loadingLabel="Sending..." />
      </form>

      <p className="mt-8 text-center text-sm text-zinc-500">
        Remembered it?{" "}
        <Link href="/login" className="font-medium text-indigo-300 transition hover:text-indigo-200">
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
