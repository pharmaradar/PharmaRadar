import { Sparkles, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

/**
 * On-demand AI synthesis panel shared by Social Trends, Discovery and Dashboard.
 * The caller owns the data fetch (so each surface controls its own query key /
 * cost) and passes the rendered "picks" section as `picks`.
 */
export function SynthesisPanel({
  takeaway, soWhat, conclusion, generatedAt, cached, error,
  isLoading, isError, hasRun, onGenerate, accent = "blue", picks,
  takeawayLabel = "Takeaway", conclusionLabel = "Conclusion",
  collapsible = false, defaultCollapsed = false, canRegenerate = true,
}: {
  takeaway?: string;
  soWhat?: string;
  conclusion?: string;
  generatedAt?: string | null;
  cached?: boolean;
  error?: string | null;
  isLoading: boolean;
  isError: boolean;
  hasRun: boolean;
  onGenerate: () => void;
  accent?: "blue" | "orange";
  picks?: React.ReactNode;
  takeawayLabel?: string;
  conclusionLabel?: string;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  canRegenerate?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const open = !collapsible || !collapsed;
  const btn = accent === "orange" ? "bg-orange-500 hover:bg-orange-600" : "bg-pharma-blue hover:bg-pharma-light";
  const accentText = accent === "orange" ? "text-orange-600 dark:text-orange-400" : "text-pharma-blue dark:text-blue-300";

  return (
    <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 p-4 space-y-3">
      <div className={cn("flex items-center gap-2", collapsible && "cursor-pointer")}
        onClick={collapsible ? () => setCollapsed(c => !c) : undefined}>
        {collapsible && (open
          ? <ChevronDown size={15} className="text-gray-400 shrink-0" />
          : <ChevronRight size={15} className="text-gray-400 shrink-0" />)}
        <Sparkles size={15} className={accentText} />
        <h3 className="text-sm font-bold text-gray-900 dark:text-[#e2e8f0]">Synthesis &amp; takeaway</h3>
        <div className="ml-auto flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
          {hasRun && generatedAt && (
            <span className="text-[10px] text-gray-400">{cached ? "cached" : "fresh"}</span>
          )}
          {/* First-time "Generate" always available; "Regenerate" hidden once the daily quota is used */}
          {(!hasRun || canRegenerate) ? (
            <button onClick={onGenerate} disabled={isLoading}
              className={cn("flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-white text-xs font-semibold disabled:opacity-50 transition-colors", btn)}>
              {isLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              {isLoading ? "Synthesizing…" : hasRun ? "Regenerate" : "Generate synthesis"}
            </button>
          ) : (
            <span className="text-[10px] text-gray-400">Daily regenerate used — resets tomorrow</span>
          )}
        </div>
      </div>

      {open && <>

      {!hasRun && !isLoading && (
        <p className="text-xs text-gray-400 leading-relaxed">
          Generate an AI synthesis of the recent feed — the key takeaway, what it means for Roche, and the most impactful posts.
        </p>
      )}
      {isError && <p className="text-xs text-red-500">Couldn't generate a synthesis — try again.</p>}
      {error && <p className="text-xs text-amber-600 dark:text-amber-400">{error}</p>}

      {takeaway && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">{takeawayLabel}</p>
          <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">{takeaway}</p>
        </div>
      )}
      {soWhat && (
        <div className={cn("rounded-lg px-3 py-2.5 border",
          accent === "orange"
            ? "bg-orange-500/5 border-orange-400/20"
            : "bg-pharma-blue/5 dark:bg-[#2563eb]/10 border-pharma-blue/20 dark:border-[#2563eb]/30")}>
          <p className={cn("text-[10px] font-semibold uppercase tracking-wide mb-1", accentText)}>So what for Roche?</p>
          <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">{soWhat}</p>
        </div>
      )}
      {conclusion && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">{conclusionLabel}</p>
          <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">{conclusion}</p>
        </div>
      )}
      {picks}
      </>}
    </div>
  );
}
