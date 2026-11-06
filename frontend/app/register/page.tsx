"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import AuthShell, { AuthField, FormError, SubmitButton } from "@/components/AuthShell";
import PasswordStrength from "@/components/PasswordStrength";
import { ApiError, register } from "@/lib/api";
import { confirmError, emailError, nameError, passwordError } from "@/lib/authRules";

// ReqGuard — Create account page (http://localhost:3000/register)

type Field = "name" | "email" | "password" | "confirm_password";
type Values = Record<Field, string>;
type Errors = Partial<Record<Field, string>>;

const FIELDS: { id: Field; label: string; type: string; autoComplete: string; placeholder: string }[] = [
  { id: "name", label: "Name", type: "text", autoComplete: "name", placeholder: "Your name" },
  { id: "email", label: "Email", type: "email", autoComplete: "email", placeholder: "you@example.com" },
  { id: "password", label: "Password", type: "password", autoComplete: "new-password", placeholder: "At least 8 characters" },
  { id: "confirm_password", label: "Confirm password", type: "password", autoComplete: "new-password", placeholder: "Type it again" },
];

function liveErrors(values: Values): Errors {
  return {
    name: nameError(values.name),
    email: emailError(values.email),
    password: passwordError(values.password, values.email),
    confirm_password: confirmError(values.password, values.confirm_password),
  };
}

export default function RegisterPage() {
  const router = useRouter();
  const [values, setValues] = useState<Values>({ name: "", email: "", password: "", confirm_password: "" });
  const [touched, setTouched] = useState<Partial<Record<Field, boolean>>>({});
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);

  const live = liveErrors(values);
  // A field stays quiet until the user leaves it, then updates on every key press
  const errorOf = (field: Field) => serverErrors[field] ?? (touched[field] ? live[field] : undefined);

  function change(field: Field, value: string) {
    setValues((v) => ({ ...v, [field]: value }));
    setServerErrors((e) => ({ ...e, [field]: undefined }));
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setFormError("");
    setTouched({ name: true, email: true, password: true, confirm_password: true });
    if (Object.values(live).some(Boolean)) return;

    setLoading(true);
    const email = values.email.trim();
    try {
      await register({ ...values, name: values.name.trim(), email });
      router.replace(`/verify-email?email=${encodeURIComponent(email)}&sent=1`);
    } catch (err) {
      setLoading(false);
      if (err instanceof ApiError && err.status === 409) setServerErrors({ email: err.message });
      else if (err instanceof ApiError && Object.keys(err.fields).length > 0) setServerErrors(err.fields);
      else setFormError(err instanceof Error ? err.message : "Something went wrong.");
    }
  }

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Create your account</h2>
      <p className="mt-1.5 text-sm text-zinc-400">Start tracing requirements to code and tests.</p>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        {FIELDS.map((f) => (
          <div key={f.id}>
            <AuthField
              id={f.id}
              label={f.label}
              type={f.type}
              autoComplete={f.autoComplete}
              placeholder={f.placeholder}
              value={values[f.id]}
              onChange={(e) => change(f.id, e.target.value)}
              onBlur={() => setTouched((t) => ({ ...t, [f.id]: true }))}
              error={errorOf(f.id)}
            />
            {f.id === "password" && <PasswordStrength password={values.password} email={values.email} />}
          </div>
        ))}

        <FormError message={formError} />
        <SubmitButton loading={loading} label="Create account" loadingLabel="Creating account..." />
      </form>

      <p className="mt-8 text-center text-sm text-zinc-500">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-indigo-300 transition hover:text-indigo-200">
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
