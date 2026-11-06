"use client";

import Link from "next/link";
import { useState } from "react";
import AuthShell, { AuthField, FormError, PrimaryLink, SubmitButton, SuccessPanel } from "@/components/AuthShell";
import PasswordStrength from "@/components/PasswordStrength";
import { ApiError, logout, resetPassword } from "@/lib/api";
import { confirmError, passwordError } from "@/lib/authRules";

export default function ResetPasswordForm({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [touched, setTouched] = useState({ password: false, confirm: false });
  const [serverError, setServerError] = useState("");
  const [formError, setFormError] = useState(token ? "" : "This reset link is incomplete. Please ask for a new one.");
  const [badLink, setBadLink] = useState(!token);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const passwordProblem = passwordError(password);
  const confirmProblem = confirmError(password, confirm);

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setFormError("");
    setTouched({ password: true, confirm: true });
    if (passwordProblem || confirmProblem) return;

    setLoading(true);
    try {
      await resetPassword(token, password, confirm);
      await logout().catch(() => undefined); // this browser's session ended with the reset
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.fields.password) setServerError(err.fields.password);
      else {
        setBadLink(err instanceof ApiError && err.status === 400);
        setFormError(err instanceof Error ? err.message : "Something went wrong.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <AuthShell>
        <SuccessPanel title="Password reset">
          Your password has been changed and every older session has been signed out.
        </SuccessPanel>
        <PrimaryLink href="/login">Go to sign in</PrimaryLink>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Choose a new password</h2>
      <p className="mt-1.5 text-sm text-zinc-400">Signing in afterwards ends all your older sessions.</p>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        <div>
          <AuthField
            id="password"
            label="New password"
            type="password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setServerError("");
            }}
            onBlur={() => setTouched((t) => ({ ...t, password: true }))}
            error={serverError || (touched.password ? passwordProblem : undefined)}
          />
          <PasswordStrength password={password} />
        </div>
        <AuthField
          id="confirm_password"
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          placeholder="Type it again"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, confirm: true }))}
          error={touched.confirm ? confirmProblem : undefined}
        />

        <FormError message={formError} />
        {badLink ? (
          <p className="text-center text-sm">
            <Link href="/forgot-password" className="font-medium text-indigo-300 transition hover:text-indigo-200">
              Ask for a new link
            </Link>
          </p>
        ) : (
          <SubmitButton loading={loading} label="Reset password" loadingLabel="Saving..." />
        )}
      </form>
    </AuthShell>
  );
}
