"use client";

import { useEffect, useMemo, useState } from "react";
import type { Requirement } from "@/lib/api";

// Scope growth: accepted requirements and change requests, counted up week by week. A requirement's week is when
// the client first asked for it (its earliest source message), not when it was extracted or reviewed.

// Categorical slots 1 and 2 of the reference palette, dark steps (validated against the zinc-950 surface)
const SERIES = [
  { key: "accepted", label: "Accepted", color: "#3987e5" },
  { key: "changes", label: "Change requests", color: "#d95926" },
] as const;

type Week = { start: Date; accepted: number; changes: number };

const HEIGHT = 168;
const PAD = { top: 12, right: 112, bottom: 24, left: 28 };
const DAY_MS = 86_400_000;

function weekStart(date: Date) {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday
  return d;
}

function askedAt(r: Requirement) {
  const times = r.sources.map((s) => new Date(s.sent_at).getTime()).filter((t) => !Number.isNaN(t));
  return new Date(times.length ? Math.min(...times) : r.created_at);
}

const weekLabel = (d: Date) => d.toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function scopeGrowth(requirements: Requirement[]): Week[] {
  const counted = requirements
    .map((r) => ({ week: weekStart(askedAt(r)).getTime(), accepted: r.status === "accepted", change: !!r.change_request_for }))
    .filter((r) => r.accepted || r.change);
  if (counted.length === 0) return [];
  const first = Math.min(...counted.map((r) => r.week));
  const last = Math.max(...counted.map((r) => r.week));
  const weeks: Week[] = [];
  let accepted = 0;
  let changes = 0;
  // Step by calendar days, so a daylight-saving change does not shift the weeks
  for (let d = new Date(first); d.getTime() <= last; d.setDate(d.getDate() + 7)) {
    const t = d.getTime();
    accepted += counted.filter((r) => r.week === t && r.accepted).length;
    changes += counted.filter((r) => r.week === t && r.change).length;
    weeks.push({ start: new Date(t), accepted, changes });
  }
  return weeks;
}

// A callback ref, so the width is measured whenever the chart's box appears (it is not there while empty)
function useWidth() {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, width] as const;
}

export default function ScopeGrowthChart({ requirements }: { requirements: Requirement[] }) {
  const weeks = useMemo(() => scopeGrowth(requirements), [requirements]);
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);

  if (weeks.length === 0) return null;
  const lastWeek = weeks[weeks.length - 1];

  const plotWidth = Math.max(width - PAD.left - PAD.right, 40);
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const max = Math.max(lastWeek.accepted, lastWeek.changes, 1);
  const step = max <= 4 ? 1 : Math.ceil(max / 4);
  const top = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: top / step + 1 }, (_, i) => i * step);
  const x = (i: number) => PAD.left + (weeks.length === 1 ? plotWidth / 2 : (i / (weeks.length - 1)) * plotWidth);
  const y = (v: number) => PAD.top + plotHeight - (v / top) * plotHeight;

  // A count holds until the next week: horizontal, then vertical
  const path = (key: "accepted" | "changes") =>
    weeks
      .map((w, i) => (i === 0 ? `M${x(0)},${y(w[key])}` : `H${x(i)}V${y(w[key])}`))
      .join("") + (weeks.length === 1 ? `m-4,0h8` : "");

  const xLabels = weeks.length <= 2 ? weeks.map((_, i) => i) : [0, Math.floor((weeks.length - 1) / 2), weeks.length - 1];

  // Direct labels at the line ends, pushed apart when the two counts are close
  const ends = SERIES.map((s) => ({ ...s, value: lastWeek[s.key], at: y(lastWeek[s.key]) }));
  if (Math.abs(ends[0].at - ends[1].at) < 14) {
    const [upper, lower] = ends[0].at <= ends[1].at ? [ends[0], ends[1]] : [ends[1], ends[0]];
    const middle = (upper.at + lower.at) / 2;
    upper.at = middle - 7;
    lower.at = middle + 7;
  }

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const fraction = (e.clientX - box.left) / box.width;
    setHover(weeks.length === 1 ? 0 : Math.round(Math.min(Math.max(fraction, 0), 1) * (weeks.length - 1)));
  }

  const hovered = hover === null ? null : weeks[hover];
  const span = `${weekLabel(weeks[0].start)} – ${weekLabel(new Date(lastWeek.start.getTime() + 6 * DAY_MS))}`;

  return (
    <section aria-labelledby="scope-growth-title" className="mt-6 rounded-2xl border border-white/10 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h2 id="scope-growth-title" className="text-sm font-medium text-zinc-200">
            Scope growth
          </h2>
          <p className="text-xs text-zinc-500">Counted up by the week the client first asked · {span}</p>
        </div>
        <ul className="flex gap-4 text-xs text-zinc-400" aria-hidden="true">
          {SERIES.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5">
              <span className="h-0.5 w-4 rounded-full" style={{ background: s.color }} />
              {s.label}
            </li>
          ))}
        </ul>
      </div>

      <div ref={ref} className="relative mt-3">
        {width > 0 && (
          <svg width={width} height={HEIGHT} role="img" aria-label={`Scope growth chart: ${lastWeek.accepted} accepted requirements and ${lastWeek.changes} change requests by the week of ${weekLabel(lastWeek.start)}`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={PAD.left + plotWidth} y1={y(t)} y2={y(t)} stroke="rgb(255 255 255 / 0.06)" />
                <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-zinc-500 text-[10px] tabular-nums">
                  {t}
                </text>
              </g>
            ))}
            {xLabels.map((i) => (
              <text
                key={i}
                x={x(i)}
                y={HEIGHT - 6}
                textAnchor={weeks.length > 1 && i === 0 ? "start" : weeks.length > 1 && i === weeks.length - 1 ? "end" : "middle"}
                className="fill-zinc-500 text-[10px]"
              >
                {weekLabel(weeks[i].start)}
              </text>
            ))}
            {hover !== null && (
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotHeight} stroke="rgb(255 255 255 / 0.25)" />
            )}
            {SERIES.map((s) => (
              <path
                key={s.key}
                d={path(s.key)}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            ))}
            {hover !== null &&
              SERIES.map((s) => (
                <circle
                  key={s.key}
                  cx={x(hover)}
                  cy={y(weeks[hover][s.key])}
                  r={4}
                  fill={s.color}
                  stroke="#09090b"
                  strokeWidth={2}
                />
              ))}
            {ends.map((s) => (
              <text key={s.key} x={x(weeks.length - 1) + 8} y={s.at} dy="0.32em" className="fill-zinc-300 text-[11px]">
                {s.label} <tspan className="fill-zinc-100 font-medium tabular-nums">{s.value}</tspan>
              </text>
            ))}
            <rect
              x={PAD.left}
              y={PAD.top}
              width={plotWidth}
              height={plotHeight}
              fill="transparent"
              onPointerMove={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </svg>
        )}
        {hovered && hover !== null && (
          <div
            role="status"
            className="pointer-events-none absolute top-0 z-10 w-44 rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-xs shadow-xl"
            style={{ left: Math.min(Math.max(x(hover) - 88, 0), Math.max(width - 176, 0)) }}
          >
            <p className="text-zinc-400">Week of {weekLabel(hovered.start)}</p>
            {SERIES.map((s) => (
              <p key={s.key} className="mt-1 flex items-center gap-2 text-zinc-300">
                <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
                {s.label}
                <span className="ml-auto font-medium tabular-nums text-zinc-100">{hovered[s.key]}</span>
              </p>
            ))}
          </div>
        )}
      </div>

      <table className="sr-only">
        <caption>Scope growth by week</caption>
        <thead>
          <tr>
            <th>Week of</th>
            <th>Accepted requirements</th>
            <th>Change requests</th>
          </tr>
        </thead>
        <tbody>
          {weeks.map((w) => (
            <tr key={w.start.getTime()}>
              <td>{weekLabel(w.start)}</td>
              <td>{w.accepted}</td>
              <td>{w.changes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
