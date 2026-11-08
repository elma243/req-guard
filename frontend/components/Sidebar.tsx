"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ShieldIcon } from "@/components/AuthShell";
import { getHealth, getMe, listProjects, logout, type Project, type User } from "@/lib/api";
import { useUnseenMessages } from "@/lib/newMessages";
import { readSelected, setSelectedProject, useSelectedProject } from "@/lib/selectedProject";

const LINKS = [
  { href: "/projects", label: "Projects" },
  { href: "/sources", label: "Sources" },
  { href: "/messages", label: "Messages" },
  { href: "/requirements", label: "Requirements" },
  { href: "/test-cases", label: "Test Cases" },
  { href: "/tasks", label: "Tasks" },
  { href: "/repository", label: "Repository" },
  { href: "/traceability", label: "Traceability" },
  { href: "/analysis", label: "Analysis" },
  { href: "/reports", label: "Reports" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const unseen = useUnseenMessages(useSelectedProject());

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-white/10 bg-zinc-900/60">
      <Link href="/projects" className="flex items-center gap-2.5 px-5 py-5">
        <ShieldIcon />
        <span className="text-lg font-semibold tracking-tight">ReqGuard</span>
      </Link>

      <ProjectPicker />

      <nav className="flex-1 space-y-0.5 px-3">
        {LINKS.map((link) => {
          const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
          const badge = link.href === "/messages" && !active ? unseen : 0;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center rounded-lg px-3 py-2 text-sm transition ${
                active
                  ? "bg-indigo-500/15 font-medium text-indigo-200"
                  : "text-zinc-400 hover:bg-white/5 hover:text-zinc-100"
              }`}
            >
              {link.label}
              {badge > 0 && (
                <span
                  className="ml-auto rounded-full bg-indigo-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white"
                  title="New messages from auto-sync"
                >
                  {badge > 99 ? "99+" : badge}
                  <span className="sr-only"> new messages</span>
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <UserBox />
      <BackendStatus />
    </aside>
  );
}

// Chooses the project that Sources, Messages and the other pages work on.
// Reloads the list on every navigation so new or deleted projects show up.
function ProjectPicker() {
  const pathname = usePathname();
  const selected = useSelectedProject();
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    listProjects()
      .then((list) => {
        setProjects(list);
        // Keep the selection valid: drop deleted/foreign projects, pick the first one if none
        const current = readSelected();
        if (!list.some((p) => String(p.id) === current)) setSelectedProject(list[0]?.id ?? null);
      })
      .catch(() => setProjects([]));
  }, [pathname]);

  if (projects === null) return <div className="mx-5 mb-3 h-9" />;

  if (projects.length === 0) {
    return (
      <Link href="/projects" className="mx-5 mb-3 block text-xs text-indigo-300 hover:text-indigo-200">
        Create a project to get started →
      </Link>
    );
  }

  return (
    <div className="mx-3 mb-3">
      <label htmlFor="project-picker" className="mb-1 block px-2 text-[11px] uppercase tracking-wider text-zinc-500">
        Project
      </label>
      <select
        id="project-picker"
        value={selected ?? ""}
        onChange={(e) => setSelectedProject(e.target.value)}
        className="w-full rounded-lg border border-white/10 bg-zinc-950/60 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-400"
      >
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}

// Logged-in user's name and a Log out button.
function UserBox() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  async function handleLogout() {
    await logout().catch(() => undefined);
    setSelectedProject(null);
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="border-t border-white/10 px-5 py-4">
      {user && (
        <div className="mb-2 min-w-0">
          <p className="truncate text-sm font-medium text-zinc-200">{user.name}</p>
          <p className="truncate text-xs text-zinc-500">{user.email}</p>
        </div>
      )}
      <button onClick={handleLogout} className="text-xs text-zinc-400 transition hover:text-zinc-100">
        Log out
      </button>
    </div>
  );
}

// Shows whether the backend /health endpoint answers (checks lib/api.ts works).
function BackendStatus() {
  const [state, setState] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    getHealth()
      .then(() => setState("online"))
      .catch(() => setState("offline"));
  }, []);

  const color = { checking: "bg-zinc-500", online: "bg-emerald-400", offline: "bg-rose-400" }[state];

  return (
    <div className="flex items-center gap-2 border-t border-white/10 px-5 py-4 text-xs text-zinc-500">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      Backend {state}
    </div>
  );
}
