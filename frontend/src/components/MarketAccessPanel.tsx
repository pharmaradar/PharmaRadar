import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Gavel, Loader2, PackageX } from "lucide-react";
import { api, type MarketAccessEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * French market access — HAS added-benefit rulings and ANSM shortages.
 *
 * ASMR is the Commission de la Transparence's verdict on how much a drug adds
 * over existing therapy, I (major) to V (none). It sets price and
 * reimbursement, so a competitor's rating is a commercial event and ours is a
 * scorecard. Ratings are shown with the French wording HAS itself uses rather
 * than a bare roman numeral, which means nothing outside France.
 *
 * Severity colour runs the opposite way to the usual convention here: a LOW
 * number is good news. Encoding that in the chip stops a reader scanning the
 * column from reading "V" as a five-star result.
 */

const RATING_STYLE: Record<string, string> = {
  I: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  II: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300",
  III: "bg-sky-50 text-sky-700 dark:bg-sky-900/20 dark:text-sky-300",
  IV: "bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300",
  V: "bg-slate-100 text-slate-600 dark:bg-slate-800/60 dark:text-slate-300",
};

function RatingChip({ e }: { e: MarketAccessEvent }) {
  const style = RATING_STYLE[(e.rating ?? "").trim()] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800/60 dark:text-slate-300";
  return (
    <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap", style)}>
      ASMR {e.rating}{e.rating_label ? ` · ${e.rating_label}` : ""}
    </span>
  );
}

function RatingSpread({ ratings, meaning }: {
  ratings: Record<string, number>;
  meaning: Record<string, { label: string; rank: number }>;
}) {
  const order = ["I", "II", "III", "IV", "V"];
  const total = order.reduce((n, k) => n + (ratings[k] ?? 0), 0);
  if (!total) return null;
  return (
    <div className="flex h-2 rounded-full overflow-hidden bg-slate-100 dark:bg-[#1e3a5f]">
      {order.map((k) => {
        const n = ratings[k] ?? 0;
        if (!n) return null;
        return (
          <div
            key={k}
            title={`ASMR ${k} (${meaning[k]?.label ?? ""}): ${n}`}
            className={cn("h-full", {
              I: "bg-emerald-500", II: "bg-emerald-400", III: "bg-sky-400",
              IV: "bg-amber-400", V: "bg-slate-400",
            }[k])}
            style={{ width: `${(n / total) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

export default function MarketAccessPanel() {
  const { data: summary } = useQuery({
    queryKey: ["market-access-summary"],
    queryFn: api.marketAccessSummary,
    staleTime: 6 * 60 * 60 * 1000,
  });
  const { data, isLoading } = useQuery({
    queryKey: ["market-access-events"],
    queryFn: () => api.marketAccessEvents(1825, undefined, undefined, 25),
    staleTime: 6 * 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="glass rounded-xl p-5 flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={14} className="animate-spin" /> Loading French market-access rulings…
      </div>
    );
  }

  const events = data?.events ?? [];

  return (
    <div className="glass rounded-xl p-5 space-y-4">
      <div className="flex items-start gap-2.5">
        <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
          <Gavel size={16} className="text-pharma-blue dark:text-blue-300" />
        </div>
        <div className="min-w-0">
          <h2 className="font-semibold text-sm">French market access</h2>
          <p className="text-xs text-gray-400">
            HAS added-benefit rulings (ASMR) and ANSM supply shortages on our portfolio and
            the competitors we track
          </p>
        </div>
      </div>

      {/* Scorecard: how France has rated each company's portfolio. */}
      {summary && summary.owners.length > 0 && (
        <div className="space-y-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            Added-benefit ratings on record
          </h3>
          {summary.owners.map((o) => (
            <div key={o.owner}>
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className={cn("text-sm",
                  o.is_ours ? "font-semibold text-pharma-blue dark:text-blue-300"
                            : "text-gray-700 dark:text-[#e2e8f0]")}>
                  {o.owner}{o.is_ours && " (us)"}
                </span>
                <span className="text-xs tabular-nums text-gray-500">{o.total} rulings</span>
              </div>
              <RatingSpread ratings={o.ratings} meaning={summary.rating_meaning} />
            </div>
          ))}
          <p className="text-[11px] text-gray-400 pt-1">
            Left to right: ASMR I (major added benefit) through V (none). A wider left
            side means France judged more of that portfolio to add real clinical value.
          </p>
        </div>
      )}

      {/* Timeline. */}
      {events.length > 0 && (
        <div className="space-y-2 pt-1">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            Recent decisions
          </h3>
          {events.slice(0, 10).map((e, i) => (
            <div
              key={i}
              className={cn(
                "rounded-lg border px-3 py-2.5",
                e.is_ours
                  ? "border-pharma-blue/30 bg-pharma-blue/5"
                  : "border-slate-200/60 dark:border-white/10",
              )}
            >
              <div className="flex items-center gap-2 flex-wrap">
                {e.kind === "shortage"
                  ? <PackageX size={12} className="text-orange-500 shrink-0" />
                  : <Gavel size={12} className="text-gray-400 shrink-0" />}
                <span className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0]">
                  {e.brand}
                </span>
                <span className="text-[11px] text-gray-400">{e.owner}</span>
                {e.kind === "asmr" && <RatingChip e={e} />}
                {e.kind === "shortage" && e.rating && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-50 text-orange-700 dark:bg-orange-900/20 dark:text-orange-300">
                    {e.rating}
                  </span>
                )}
                <span className="text-[11px] text-gray-400 ml-auto tabular-nums">
                  {e.event_date}
                </span>
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer"
                     className="text-gray-400 hover:text-pharma-blue" title="Open the official decision">
                    <ExternalLink size={11} />
                  </a>
                )}
              </div>
              {e.summary && (
                <p className="text-xs text-gray-600 dark:text-[#94a3b8] mt-1.5 line-clamp-3">
                  {e.summary}
                </p>
              )}
              {e.presentations > 1 && (
                <p className="text-[10px] text-gray-400 mt-1">
                  Covers {e.presentations} presentations of this drug
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {events.length === 0 && (
        <p className="text-sm text-gray-400">
          No rulings recorded yet — the daily sync runs at 04:40.
        </p>
      )}

      <p className="text-[11px] text-gray-400 pt-3 border-t border-slate-200/50 dark:border-white/5">
        Source: Base de Données Publique des Médicaments (HAS Commission de la Transparence,
        ANSM){summary?.synced_at && ` · synced ${new Date(summary.synced_at).toLocaleDateString("en-GB")}`}.
      </p>
    </div>
  );
}
