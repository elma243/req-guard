"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import AuthShell, { AuthField, FormError, PrimaryLink, SubmitButton, SuccessPanel } from "@/components/AuthShell";
import OtpInput, { emptyOtp } from "@/components/OtpInput";
import { ApiError, resendCode, verifyEmail } from "@/lib/api";
import { OTP_LENGTH, emailError } from "@/lib/authRules";

const RESEND_COOLDOWN_SECONDS = 60; // same as the backend (app/api/auth.py)

export default function VerifyEmailForm({ initialEmail, justSent }: { initialEmail: string; justSent: boolean }) {
  const [email, setEmail] = useState(initialEmail);
  const [digits, setDigits] = useState(emptyOtp);
  const [emailProblem, setEmailProblem] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [cooldown, setCooldown] = useState(justSent ? RESEND_COOLDOWN_SECONDS : 0);
  const [verified, setVerified] = useState(false);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  // Both actions need a valid address; it is only editable when the page was opened without one
  function validEmail() {
    const problem = emailError(email) ?? "";
    setEmailProblem(problem);
    return !problem;
  }

  async function submit(code: string) {
    setFormError("");
    setNotice("");
    if (!validEmail()) return;
    if (code.length !== OTP_LENGTH) {
      setFormError("Enter the 6-digit code.");
      return;
    }

    setLoading(true);
    try {
      await verifyEmail(email.trim(), code);
      setVerified(true);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Something went wrong.");
      setDigits(emptyOtp());
    } finally {
      setLoading(false);
    }
  }

  function changeDigits(next: string[]) {
    setDigits(next);
    if (next.every(Boolean) && !loading) submit(next.join("")); // the last digit sends the code
  }

  async function handleResend() {
    setFormError("");
    setNotice("");
    if (!validEmail()) return;

    setResending(true);
    try {
      const { message } = await resendCode(email.trim());
      setNotice(message);
      setDigits(emptyOtp());
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      if (err instanceof ApiError && err.retryAfter) setCooldown(err.retryAfter);
      setFormError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setResending(false);
    }
  }

  if (verified) {
    return (
      <AuthShell>
        <SuccessPanel title="Email verified">Your account is ready. Sign in to start using ReqGuard.</SuccessPanel>
        <PrimaryLink href="/login">Go to sign in</PrimaryLink>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Verify your email</h2>
      <p className="mt-1.5 text-sm text-zinc-400">
        {initialEmail ? (
          <>
            Enter the 6-digit code we sent to <span className="wrap-break-word text-zinc-200">{initialEmail}</span>. It expires
            in 10 minutes.
          </>
        ) : (
          "Enter your email and the 6-digit code we sent you. It expires in 10 minutes."
        )}
      </p>

      <form
        onSubmit={(ev) => {
          ev.preventDefault();
          submit(digits.join(""));
        }}
        noValidate
        className="mt-8 space-y-5"
      >
        {!initialEmail && (
          <AuthField
            id="email"
            label="Email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setEmailProblem("");
            }}
            error={emailProblem}
          />
        )}

        <OtpInput
          value={digits}
          onChange={changeDigits}
          hasError={!!formError}
          disabled={loading}
          autoFocus={!!initialEmail}
        />

        <FormError message={formError} />
        {notice && (
          <p role="status" className="text-sm text-emerald-300">
            {notice}
          </p>
        )}
        <SubmitButton loading={loading} label="Verify email" loadingLabel="Verifying..." />
      </form>

      <div className="mt-6 text-center text-sm text-zinc-500">
        Didn&apos;t get it?{" "}
        <button
          type="button"
          onClick={handleResend}
          disabled={cooldown > 0 || resending}
          className="font-medium text-indigo-300 transition hover:text-indigo-200 disabled:cursor-not-allowed disabled:text-zinc-500"
        >
          {cooldown > 0 ? `Resend code in ${cooldown}s` : resending ? "Sending..." : "Resend code"}
        </button>
      </div>

      <p className="mt-4 text-center text-sm text-zinc-500">
        <Link href="/login" className="font-medium text-indigo-300 transition hover:text-indigo-200">
          Back to sign in
        </Link>
      </p>
    </AuthShell>
  );
}
