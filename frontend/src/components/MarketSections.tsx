import { AlertTriangle } from "lucide-react";
import type { MarketReportVoiceRow, MarketReportVolume } from "@/lib/api";

/**
 * The market-research sections shared by Topic Explorer and Burning Topics.
 *
 * Voice distribution and volume are computed server-side from the underlying
 * rows, so they render as figures with their caveats attached rather than as
 * prose the model wrote. The caveats are deliberately visible: a chart that
 * presents inference as fact is worse than no chart, because it gets acted on.
 */

export function Prose({ text, empty = "Not enough material for this section." }: {
  text?: string | null; empty?: string;
}) {
  if (!text?.trim()) return <p className="text-sm text-gray-400">{empty}</p>;
  return (
    <div className="space-y-2">
      {text.split("\n").filter((p) => p.trim()).map((p, i) => (
        <p key={i} className="text-sm leading-relaxed text-gray-700 dark:text-[#e2e8f0]">{p}</p>
      ))}
    </div>
  );
}

export function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-[11px] text-amber-700 dark:text-amber-400 bg-amber-50/70 dark:bg-amber-900/10 border border-amber-200/60 dark:border-amber-900/30 rounded-lg px-2.5 py-1.5">
      <AlertTriangle size={12} className="shrink-0 mt-px" />
      <span>{children}</span>
    </p>
  );
}

export function VoiceChart({ rows, exactShare }: {
  rows: MarketReportVoiceRow[]; exactShare: number;
}) {
  if (!rows?.length) {
    return <p className="text-sm text-gray-400">No attributable voices in this material.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.bucket} className="flex items-center gap-2">
            <span className="w-44 shrink-0 text-xs text-gray-600 dark:text-[#94a3b8] truncate" title={row.label}>
              {row.label}
            </span>
            <div className="flex-1 h-3 bg-slate-100 dark:bg-white/5 rounded overflow-hidden">
              <div className="h-3 bg-pharma-blue rounded" style={{ width: `${Math.max(row.percent, 2)}%` }} />
            </div>
            <span className="w-20 shrink-0 text-xs text-right text-gray-600 dark:text-[#94a3b8]">
              <b>{row.mentions}</b> ({row.percent}%)
            </span>
          </div>
        ))}
      </div>
      <Caveat>
        {exactShare}% identified from tracked records (KOL targets, curated sources).
        The rest is inferred from the author name — read as indicative.
      </Caveat>
    </div>
  );
}

export function VolumeBlock({ volume }: { volume: Partial<MarketReportVolume> }) {
  if (!volume?.total) return <p className="text-sm text-gray-400">No mentions in this window.</p>;
  const weeks = Object.entries(volume.per_week || {}).slice(-8);
  const peak = Math.max(1, ...weeks.map(([, count]) => count));
  const coverage = volume.date_coverage ?? 100;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3 text-sm">
        <span className="text-gray-700 dark:text-[#e2e8f0]"><b>{volume.total}</b> mentions</span>
        {Object.entries(volume.by_kind || {}).map(([kind, count]) => (
          <span key={kind} className="text-xs text-gray-500 dark:text-[#94a3b8]">{kind}: {count}</span>
        ))}
      </div>
      {weeks.length > 0 && (
        <div className="flex items-end gap-1 h-16">
          {weeks.map(([week, count]) => (
            <div key={week} className="flex-1 flex flex-col items-center gap-1" title={`week of ${week}: ${count}`}>
              <div className="w-full bg-pharma-blue/70 rounded-t" style={{ height: `${(count / peak) * 100}%` }} />
              <span className="text-[9px] text-gray-400">{week.slice(5)}</span>
            </div>
          ))}
        </div>
      )}
      {coverage < 100 && (
        <Caveat>
          {volume.dated} of {volume.total} mentions carry a usable date ({coverage}%).
          The trend above covers only those — search-sourced posts often arrive undated.
        </Caveat>
      )}
    </div>
  );
}

export function SubtopicList({ items }: { items: string[] }) {
  if (!items?.length) return <p className="text-sm text-gray-400">None identified.</p>;
  return (
    <ul className="space-y-1.5">
      {items.map((topic, i) => (
        <li key={i} className="flex items-start gap-2 text-sm text-gray-700 dark:text-[#e2e8f0]">
          <span className="w-1.5 h-1.5 rounded-full bg-pharma-blue mt-1.5 shrink-0" />
          <span>{topic}</span>
        </li>
      ))}
    </ul>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
      {children}
    </div>
  );
}
