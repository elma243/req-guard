"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import AuthShell, { FormError, SubmitButton, authInputClass } from "@/components/AuthShell";
import { register } from "@/lib/api";

// ReqGuard — Create account page (http://localhost:3000/register)

type Fields = "name" | "email" | "password" | "confirm";

export default function RegisterPage() {
  const router = useRouter();
  const [values, setValues] = useState<Record<Fields, string>>({ name: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Partial<Record<Fields, string>>>({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);

  const set = (field: Fields) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((v) => ({ ...v, [field]: e.target.value }));

  // Same rules as the backend (app/schemas/auth.py)
  function validate() {
    const e: Partial<Record<Fields, string>> = {};
    if (!values.name.trim()) e.name = "Name is required.";
    else if (values.name.trim().length > 100) e.name = "Name must be at most 100 characters.";
    if (!values.email.trim()) e.email = "Email is required.";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) e.email = "Enter a valid email address.";
    if (values.password.length < 8) e.password = "Password must be at least 8 characters.";
    else if (values.password.length > 128) e.password = "Password must be at most 128 characters.";
    if (values.confirm !== values.password) e.confirm = "Passwords do not match.";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setFormError("");
    if (!validate()) return;

    setLoading(true);
    try {
      await register({ name: values.name.trim(), email: values.email.trim(), password: values.password, confirm_password: values.confirm });
      router.replace("/projects");
      router.refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Something went wrong.");
      setLoading(false);
    }
  }

  const fields: { id: Fields; label: string; type: string; autoComplete: string; placeholder: string }[] = [
    { id: "name", label: "Name", type: "text", autoComplete: "name", placeholder: "Your name" },
    { id: "email", label: "Email", type: "email", autoComplete: "email", placeholder: "you@example.com" },
    { id: "password", label: "Password", type: "password", autoComplete: "new-password", placeholder: "At least 8 characters" },
    { id: "confirm", label: "Confirm password", type: "password", autoComplete: "new-password", placeholder: "Type it again" },
  ];

  return (
    <AuthShell>
      <h2 className="text-2xl font-semibold tracking-tight">Create your account</h2>
      <p className="mt-1.5 text-sm text-zinc-400">Start tracing requirements to code and tests.</p>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        {fields.map((f) => (
          <div key={f.id}>
            <label htmlFor={f.id} className="block text-sm font-medium text-zinc-300">
              {f.label}
            </label>
            <input
              id={f.id}
              type={f.type}
              autoComplete={f.autoComplete}
              value={values[f.id]}
              onChange={set(f.id)}
              placeholder={f.placeholder}
              aria-invalid={!!errors[f.id]}
              className={`mt-1.5 ${authInputClass(!!errors[f.id])}`}
            />
            {errors[f.id] && <p className="mt-1.5 text-xs text-rose-400">{errors[f.id]}</p>}
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
