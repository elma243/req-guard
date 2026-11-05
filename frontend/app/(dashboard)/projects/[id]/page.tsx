"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ModulesEditor from "@/components/ModulesEditor";
import { ErrorBanner, Label, dangerButton, errorText, inputClass, primaryButton } from "@/components/ui";
import {
  deleteProject,
  getProject,
  updateProject,
  type Project,
  type ProjectInput,
  type ProjectModule,
} from "@/lib/api";
import { setSelectedProject } from "@/lib/selectedProject";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [form, setForm] = useState<ProjectInput>({ name: "", description: "", scope_text: "" });
  const [modules, setModules] = useState<ProjectModule[]>([]);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getProject(id)
      .then((p) => {
        setProject(p);
        setSelectedProject(p.id); // opening a project also makes it the sidebar selection
        setForm({ name: p.name, description: p.description, scope_text: p.scope_text });
        setModules(p.modules);
      })
      .catch((err) => setLoadError(errorText(err)));
  }, [id]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      setError("Project name is required.");
      return;
    }
    // Empty rows are dropped; a description without a name is a mistake
    const filled = modules.filter((m) => m.name.trim() || m.description.trim());
    if (filled.some((m) => !m.name.trim())) {
      setError("Every module needs a name.");
      return;
    }
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const p = await updateProject(id, { ...form, name: form.name.trim(), modules: filled });
      setProject(p);
      setModules(p.modules);
      setSaved(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!project || !confirm(`Delete "${project.name}" and its team? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await deleteProject(id);
      router.push("/projects");
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className="max-w-3xl space-y-4">
        <ErrorBanner message={loadError} />
        <Link href="/projects" className="text-sm text-indigo-300 hover:text-indigo-200">
          ← Back to projects
        </Link>
      </div>
    );
  }
  if (!project) return <p className="text-sm text-zinc-500">Loading project...</p>;

  return (
    <div className="max-w-3xl">
      <Link href="/projects" className="text-sm text-zinc-400 hover:text-zinc-100">
        ← Projects
      </Link>
      <div className="mt-3 flex items-start justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
        <Link href={`/projects/${id}/team`} className={primaryButton}>
          Team
        </Link>
      </div>

      <form onSubmit={handleSave} className="mt-6 space-y-4 rounded-2xl border border-white/10 bg-zinc-900/60 p-6">
        <div>
          <Label htmlFor="name">Name</Label>
          <input
            id="name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            maxLength={200}
            className={inputClass}
          />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <textarea
            id="description"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            rows={3}
            className={inputClass}
          />
        </div>
        <div>
          <Label htmlFor="scope">Scope</Label>
          <textarea
            id="scope"
            value={form.scope_text}
            onChange={(e) => setForm({ ...form, scope_text: e.target.value })}
            rows={6}
            className={inputClass}
          />
        </div>
        <ModulesEditor modules={modules} onChange={setModules} />
        <ErrorBanner message={error} />
        {saved &&<p className="text-sm text-emerald-300">Saved.</p>}
        <div className="flex justify-between gap-3">
          <button type="submit" disabled={busy} className={primaryButton}>
            Save changes
          </button>
          <button type="button" onClick={handleDelete} disabled={busy} className={dangerButton}>
            Delete project
          </button>
        </div>
      </form>
    </div>
  );
}
