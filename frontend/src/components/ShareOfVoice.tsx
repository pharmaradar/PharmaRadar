import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, PieChart } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Share of voice by product.
 *
 * A brand lead does not think in topics, they think in assets: is the
 * conversation about Tecentriq or Keytruda, and are we gaining or losing? Every
 * figure here is counted from text already stored — no scraping, no LLM call —
 * so it costs nothing and moves the moment a run lands.
 *
 * Net sentiment is shown over RATED mentions only. A brand discussed forty times
 * without an opinion attached is "unrated", not "0% positive"; collapsing those
 * two would invent a negative signal.
 */

const SOURCES = [
  { value: "all", label: "All sources" },
  { value: "kol", label: "KOL only" },
  { value: "social", label: "Social only" },
];

const PERIODS = [30, 90, 180];

export default function ShareOfVoice() {
  const [days, setDays] = useState(30);
  const [source, setSource] = useState("all");

  const { data, isLoading } = useQuery({
    queryKey: ["share-of-voice", days, source],
    queryFn: () => api.shareOfVoice(days, source),
    staleTime: 5 * 60 * 1000,
  });

  const brands = data?.brands ?? [];
  const peak = Math.max(1, ...brands.map((b) => b.mentions));

  return (
    <div className="glass rounded-xl p-5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <PieChart size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Share of voice by product</h2>
            <p className="text-xs text-gray-400">
              Which assets the market is talking about — ours against the competition.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <select value={source} onChange={(e) => setSource(e.target.value)}
            className="px-2 py-1 text-xs rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">
            {SOURCES.map((s) => (
              <option key={s.value} value={s.value} className="dark:bg-[#0d1424]">{s.label}</option>
            ))}
          </select>
          <div className="flex gap-1">
            {PERIODS.map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={cn("px-2 py-1 rounded-lg text-xs font-medium transition-colors",
                  days === d ? "bg-pharma-blue text-white" : "text-gray-500 hover:text-pharma-light")}>
                {d}d
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />Counting brand mentions…
        </p>
      ) : !data || data.total_mentions === 0 ? (
        <p className="text-sm text-gray-400">
          No tracked product was mentioned in this window. {data ? `${data.items_scanned} items scanned.` : ""}
        </p>
      ) : (
        <>
          {/* Headline: our share against everyone else. */}
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-2xl font-bold text-pharma-blue dark:text-blue-300">
              {data.roche_share}%
            </span>
            <span className="text-sm text-gray-600 dark:text-[#94a3b8]">
              Roche share of voice — {data.roche_mentions} of {data.total_mentions} mentions
            </span>
            <span className="text-[11px] text-gray-400">
              {data.items_scanned} items · {data.tracked_brands} brands tracked
            </span>
          </div>

          <div className="flex h-2.5 rounded-full overflow-hidden bg-slate-100 dark:bg-white/5">
            {data.by_owner.map((owner) => (
              <div key={owner.owner}
                title={`${owner.owner}: ${owner.mentions} mentions (${owner.share}%)`}
                style={{ width: `${owner.share}%` }}
                className={cn(owner.is_ours ? "bg-pharma-blue" : "bg-slate-300 dark:bg-slate-600")} />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {data.by_owner.map((owner) => (
              <span key={owner.owner} className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-[#94a3b8]">
                <span className={cn("w-2 h-2 rounded-sm",
                  owner.is_ours ? "bg-pharma-blue" : "bg-slate-300 dark:bg-slate-600")} />
                {owner.owner} {owner.share}%
              </span>
            ))}
          </div>

          {/* Per-brand detail. */}
          <div className="space-y-1.5 pt-1">
            {brands.map((b) => (
              <div key={b.brand} className="flex items-center gap-2">
                <span className={cn("w-28 shrink-0 text-xs truncate",
                  b.is_ours ? "font-semibold text-pharma-blue dark:text-blue-300"
                            : "text-gray-600 dark:text-[#94a3b8]")}
                  title={`${b.brand} — ${b.owner}${b.indication ? ` · ${b.indication}` : ""}`}>
                  {b.brand}
                </span>
                <div className="flex-1 h-2.5 bg-slate-100 dark:bg-white/5 rounded overflow-hidden">
                  <div className={cn("h-2.5 rounded",
                    b.is_ours ? "bg-pharma-blue" : "bg-slate-300 dark:bg-slate-600")}
                    style={{ width: `${Math.max((b.mentions / peak) * 100, 3)}%` }} />
                </div>
                <span className="w-12 shrink-0 text-xs text-right text-gray-600 dark:text-[#94a3b8]">
                  {b.mentions}
                </span>
                <span className="w-20 shrink-0 text-[11px] text-right"
                  title={b.net_sentiment === null
                    ? "No mention carried a rated opinion"
                    : `${b.rated_mentions} rated mentions`}>
                  {b.net_sentiment === null ? (
                    <span className="text-gray-400">unrated</span>
                  ) : (
                    <span className={b.net_sentiment >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-red-500"}>
                      {b.net_sentiment > 0 ? "+" : ""}{b.net_sentiment}%
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-gray-400">
            Share is of total brand mentions; an item naming two products counts for both.
            Net sentiment covers rated mentions only.
          </p>
        </>
      )}
    </div>
  );
}
