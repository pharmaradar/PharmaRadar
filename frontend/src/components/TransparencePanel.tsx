import { useQuery } from "@tanstack/react-query";
import { AlertCircle, BadgeEuro, Info, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Declared industry payments to one KOL — France's Sunshine Act register.
 *
 * The panel's job is as much to withhold as to show. Figures render only when
 * the backend pinned this person to exactly one national RPPS identifier; an
 * ambiguous match shows the reason and no numbers, because a payment
 * attributed to the wrong clinician in a competitive brief is invisible to the
 * reader and worse than a gap.
 *
 * The identifier and the sync date are always visible for the same reason: a
 * euro total means nothing unless you can see whose it is and how current.
 */

const EUR = new Intl.NumberFormat("fr-FR", {
  style: "currency", currency: "EUR", maximumFractionDigits: 0,
});

/** Roche's own SIREN. Matching on the name would also catch ROCHE SUISSE and
 *  ROCHE MAROC, which are separate legal entities outside the French remit. */
const ROCHE_SIREN = "552012031";

function Bar({ pct, ours }: { pct: number; ours: boolean }) {
  return (
    <div className="h-1.5 rounded-full bg-slate-100 dark:bg-[#1e3a5f] overflow-hidden">
      <div
        className={cn("h-full rounded-full", ours ? "bg-pharma-blue" : "bg-orange-400")}
        style={{ width: `${Math.max(pct, 1.5)}%` }}
      />
    </div>
  );
}

export default function TransparencePanel({ targetId }: { targetId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["transparence", targetId],
    queryFn: () => api.transparenceTarget(targetId),
    staleTime: 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="glass rounded-xl p-5 flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={14} className="animate-spin" /> Loading declared payments…
      </div>
    );
  }
  if (!data) return null;

  const top = data.companies[0]?.total_eur ?? 0;

  return (
    <div className="glass rounded-xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <BadgeEuro size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Industry payments declared</h2>
            <p className="text-xs text-gray-400">
              Transparence Santé — France's public register of payments from companies to
              healthcare professionals
            </p>
          </div>
        </div>
        {data.displayable && (
          <div className="text-right shrink-0">
            <div className="text-lg font-semibold tabular-nums text-gray-800 dark:text-[#e2e8f0]">
              {EUR.format(data.total_eur)}
            </div>
            <div className="text-[11px] text-gray-400 tabular-nums">
              {data.payment_count.toLocaleString("fr-FR")} declarations
            </div>
          </div>
        )}
      </div>

      {/* Not resolved — say why, show nothing else. */}
      {!data.displayable && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 dark:border-amber-900/40 bg-amber-50/60 dark:bg-amber-900/10 px-3 py-2.5">
          <AlertCircle size={14} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div className="text-xs text-amber-800 dark:text-amber-200">
            <p className="font-medium">
              {data.status === "not_found"
                ? "No declarations found for this target"
                : "Identity not confirmed — no figures shown"}
            </p>
            {data.note && <p className="mt-0.5 opacity-90">{data.note}</p>}
            {data.status === "ambiguous" && (
              <p className="mt-1 opacity-90">
                Several people share this name in the register. Showing one of them
                could attribute another clinician's payments to this KOL, so nothing
                is displayed.
              </p>
            )}
          </div>
        </div>
      )}

      {data.displayable && (
        <>
          <div>
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                Who pays them
              </h3>
              <span className="text-[11px] text-gray-400">by company, last 10 years</span>
            </div>
            <div className="space-y-2.5">
              {data.companies.slice(0, 8).map((c) => {
                const ours = c.siren === ROCHE_SIREN;
                return (
                  <div key={c.siren ?? c.company}>
                    <div className="flex items-baseline justify-between gap-2 mb-1">
                      <span className={cn("text-sm truncate",
                        ours ? "font-semibold text-pharma-blue dark:text-blue-300"
                             : "text-gray-700 dark:text-[#e2e8f0]")}>
                        {c.company}{ours && " (us)"}
                      </span>
                      <span className="text-sm tabular-nums text-gray-600 dark:text-[#94a3b8] shrink-0">
                        {EUR.format(c.total_eur)}
                      </span>
                    </div>
                    <Bar pct={top ? (c.total_eur / top) * 100 : 0} ours={ours} />
                  </div>
                );
              })}
            </div>
          </div>

          {data.recent.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                Most recent declarations
              </h3>
              <div className="space-y-1.5">
                {data.recent.slice(0, 5).map((p, i) => (
                  <div key={i} className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="text-gray-600 dark:text-[#94a3b8] truncate">
                      {p.paid_on ?? "undated"} · {p.company}
                      {p.reason && <span className="text-gray-400"> · {p.reason}</span>}
                    </span>
                    <span className="tabular-nums text-gray-700 dark:text-[#e2e8f0] shrink-0">
                      {EUR.format(p.amount_eur)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Provenance. Always shown, including on the refusals — it is the reason
          the figures above can be trusted at all. */}
      <div className="flex items-start gap-1.5 pt-3 border-t border-slate-200/50 dark:border-white/5 text-[11px] text-gray-400">
        <Info size={11} className="shrink-0 mt-0.5" />
        <p>
          {data.rpps ? (
            <>
              Matched to national identifier <span className="font-mono">{data.rpps}</span>
              {data.confidence != null && ` (${Math.round(data.confidence * 100)}% of declarations)`}.{" "}
            </>
          ) : null}
          {data.synced_at
            ? `Synced ${new Date(data.synced_at).toLocaleDateString("en-GB")}.`
            : "Not synced yet."}{" "}
          Source: Ministère de la Santé, updated daily.
        </p>
      </div>
    </div>
  );
}
