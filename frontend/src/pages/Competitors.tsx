import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useGenQuota } from "@/hooks/useGenQuota";

const PERIODS = [
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
  { label: "180 days", value: 180 },
];

export default function Competitors() {
  const qc = useQueryClient();
  const [days, setDays] = useState(90);
  const { can: canGen } = useGenQuota();

  const { data: targets } = useQuery({ queryKey: ["targets"], queryFn: api.targets.list });
  const competitors = (targets ?? []).filter((t) => t.target_type === "competitor");

  const { data: brief, isLoading: briefLoading } = useQuery({
    queryKey: ["competitor-brief"],
    queryFn: () => api.competitorBrief(),
    staleTime: 6 * 60 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const briefMut = useMutation({
    mutationFn: () => api.competitorBrief(true),
    onSuccess: (data) => { qc.setQueryData(["competitor-brief"], data); qc.invalidateQueries({ queryKey: ["gen-quota"] }); },
  });

  const { data: pubs, isLoading: pubsLoading } = useQuery({
    queryKey: ["competitor-publications", days],
    queryFn: () => api.competitorPublications(days, 25),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Building2 size={22} className="text-orange-500" />
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Competitors</h1>
        </div>
        <span className="text-sm text-slate-500">{competitors.length} tracked competitor{competitors.length !== 1 ? "s" : ""}</span>
      </div>

      <p className="text-sm text-slate-500 dark:text-slate-400 -mt-2">
        Competitor accounts run through the same scrape/extract pipeline as KOLs, but their content is
        kept out of the KOL brief. Add competitors from the Targets page (type: Competitor).
      </p>

      {/* Tracked competitor list */}
      {competitors.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {competitors.map((c) => (
            <div key={c.id} className={cn(
              "px-3 py-1.5 rounded-lg border text-sm",
              c.active
                ? "border-orange-200 dark:border-orange-800/50 bg-orange-50/60 dark:bg-orange-900/10 text-orange-700 dark:text-orange-300"
                : "border-slate-200 dark:border-white/10 text-slate-400"
            )}>
              {c.name}{!c.active && " (inactive)"}
            </div>
          ))}
        </div>
      )}

      {/* Competitor brief */}
      <div className="glass rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="font-semibold text-sm">Competitor Intelligence Brief</h2>
            <p className="text-xs text-gray-400">What rival companies are launching, claiming, and signalling — last 6 months</p>
          </div>
          {canGen("competitor_brief") && (
            <button onClick={() => briefMut.mutate()} disabled={briefMut.isPending || briefLoading}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-orange-300 dark:border-orange-800 text-orange-600 dark:text-orange-400 rounded-lg hover:bg-orange-50 dark:hover:bg-orange-900/20 disabled:opacity-50">
              {briefMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Regenerate
            </button>
          )}
        </div>
        {briefLoading || briefMut.isPending ? (
          <div className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={14} className="animate-spin" />Analysing competitor content…</div>
        ) : brief && brief.points.length > 0 ? (
          <div className="space-y-2">
            {brief.points.map((p, i) => (
              <div key={i} className="flex items-start gap-3 p-3 rounded-xl border bg-orange-50/40 dark:bg-orange-900/5 border-orange-200/50 dark:border-orange-800/20">
                <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 bg-orange-500" />
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{p.text}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">{brief?.error || "No competitor insights yet — add competitor targets and run a scrape."}</p>
        )}
      </div>

      {/* Top publications by engagement */}
      <div className="glass rounded-xl p-5">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div>
            <h2 className="font-semibold text-sm">Top publications by engagement</h2>
            <p className="text-xs text-gray-400">
              Ranked by likes + views where the scrape captured them; recency otherwise
            </p>
          </div>
          <div className="inline-flex rounded-lg border border-slate-200 dark:border-[#1e3a5f] p-0.5">
            {PERIODS.map((p) => (
              <button key={p.value} onClick={() => setDays(p.value)}
                className={cn("px-2.5 py-1 text-xs rounded-md",
                  days === p.value ? "bg-pharma-blue text-white" : "text-slate-500")}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        {pubsLoading ? (
          <div className="text-sm text-gray-400 py-3">Loading…</div>
        ) : !pubs || pubs.publications.length === 0 ? (
          <div className="text-sm text-gray-400 py-3">
            No competitor publications in the last {days} days — run a scrape with competitor targets active.
          </div>
        ) : (
          <div className="space-y-2">
            {pubs.publications.map((p) => (
              <a key={p.id} href={p.url} target="_blank" rel="noreferrer"
                 className="block rounded-lg border border-slate-200/60 dark:border-white/10 px-3 py-2.5 hover:border-orange-400/50 transition-colors group">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-orange-600">
                      {p.title || p.excerpt.slice(0, 100) || p.url}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {p.competitor} · {p.source} · {p.published_date || "date unknown"}
                      {p.engagement > 0 && <> · {p.likes.toLocaleString()} likes · {p.views.toLocaleString()} views</>}
                    </div>
                  </div>
                  <ExternalLink size={14} className="text-slate-300 group-hover:text-orange-500 shrink-0 mt-1" />
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
