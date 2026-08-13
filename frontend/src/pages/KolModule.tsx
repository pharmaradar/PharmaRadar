import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ExternalLink, Linkedin, Loader2, Search, TrendingUp, Twitter, Users, X,
} from "lucide-react";
import { api, type KolProfileCard } from "@/lib/api";
import ShareOfVoice from "@/components/ShareOfVoice";
import { cn } from "@/lib/utils";

/**
 * The KOL module: one page to browse every tracked person, search them, and read
 * what each has been saying.
 *
 * The pipeline has written a per-KOL summary every run since the beginning, but
 * nothing read it back except the PDF generator — the spec's "individual sum up
 * for each KOL (with research bar)" had no screen. This is that screen; no new
 * data is generated for it.
 */

const SENTIMENT_STYLE: Record<string, string> = {
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-red-500",
  neutral: "text-gray-500 dark:text-[#94a3b8]",
};

const PERIODS = [30, 90, 365];

function SentimentBar({ sentiment }: { sentiment: Record<string, number> }) {
  const total = Object.values(sentiment).reduce((a, b) => a + b, 0);
  if (!total) return null;
  const order: [string, string][] = [
    ["positive", "bg-emerald-500"], ["neutral", "bg-slate-300 dark:bg-slate-600"], ["negative", "bg-red-500"],
  ];
  return (
    <div className="space-y-1">
      <div className="flex h-2 rounded-full overflow-hidden bg-slate-100 dark:bg-white/5">
        {order.map(([key, colour]) => {
          const value = sentiment[key] || 0;
          if (!value) return null;
          return <div key={key} className={colour} style={{ width: `${(value / total) * 100}%` }}
            title={`${key}: ${value}`} />;
        })}
      </div>
      <div className="flex gap-3">
        {order.map(([key]) => (sentiment[key] ? (
          <span key={key} className={cn("text-[11px]", SENTIMENT_STYLE[key])}>
            {key} {sentiment[key]}
          </span>
        ) : null))}
      </div>
    </div>
  );
}

function ProfileDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const [days, setDays] = useState(90);
  const { data: profile, isLoading } = useQuery({
    queryKey: ["kol-profile", id, days],
    queryFn: () => api.kolProfile(id, days),
  });

  const weeks = Object.entries(profile?.per_week ?? {}).slice(-12);
  const peak = Math.max(1, ...weeks.map(([, c]) => c));

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-2xl h-full overflow-y-auto overlay-panel border-l p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-bold text-pharma-blue dark:text-[#e2e8f0] truncate">
              {profile?.name ?? "Loading…"}
            </h2>
            <div className="flex items-center gap-2 flex-wrap mt-1">
              {profile?.disease_area && (
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-slate-500">
                  {profile.disease_area.replace(/_/g, " ")}
                </span>
              )}
              {profile?.twitter_handle && (
                <a href={`https://x.com/${profile.twitter_handle.replace("@", "")}`}
                  target="_blank" rel="noreferrer" className="text-gray-400 hover:text-pharma-blue">
                  <Twitter size={13} />
                </a>
              )}
              {profile?.linkedin_url && (
                <a href={profile.linkedin_url} target="_blank" rel="noreferrer"
                  className="text-gray-400 hover:text-pharma-blue"><Linkedin size={13} /></a>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="flex gap-1">
              {PERIODS.map((d) => (
                <button key={d} onClick={() => setDays(d)}
                  className={cn("px-2 py-1 rounded-lg text-xs font-medium",
                    days === d ? "bg-pharma-blue text-white" : "text-gray-500 hover:text-pharma-light")}>
                  {d}d
                </button>
              ))}
            </div>
            <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200">
              <X size={18} />
            </button>
          </div>
        </div>

        {isLoading || !profile ? (
          <p className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin" />Loading profile…
          </p>
        ) : (
          <>
            {profile.summary_bullets.length > 0 ? (
              <div className="space-y-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Summary of what they said
                </div>
                <ul className="space-y-1.5">
                  {profile.summary_bullets.map((bullet, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-gray-700 dark:text-[#e2e8f0]">
                      <span className="w-1.5 h-1.5 rounded-full bg-pharma-blue mt-1.5 shrink-0" />
                      <span>{bullet}</span>
                    </li>
                  ))}
                </ul>
                {profile.summary_generated_at && (
                  <p className="text-[11px] text-gray-400">
                    Generated {new Date(profile.summary_generated_at).toLocaleDateString()}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-gray-400">
                No summary yet — one is written for each KOL on every pipeline run.
              </p>
            )}

            {profile.so_what && (
              <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
                <div className="text-[10px] font-semibold text-amber-700 dark:text-amber-300 uppercase tracking-wider mb-1">
                  So what for pharma
                </div>
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] whitespace-pre-wrap">{profile.so_what}</p>
              </div>
            )}

            <div className="grid sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Sentiment · {profile.insight_count} statements
                </div>
                <SentimentBar sentiment={profile.sentiment} />
              </div>
              {weeks.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Activity</div>
                  <div className="flex items-end gap-1 h-12">
                    {weeks.map(([week, count]) => (
                      <div key={week} className="flex-1 bg-pharma-blue/70 rounded-t"
                        style={{ height: `${(count / peak) * 100}%` }}
                        title={`week of ${week}: ${count}`} />
                    ))}
                  </div>
                </div>
              )}
            </div>

            {profile.top_topics.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Topics they raise</div>
                <div className="flex flex-wrap gap-1.5">
                  {profile.top_topics.map((t) => (
                    <span key={t.topic}
                      className="px-2 py-0.5 rounded-lg border border-slate-200 dark:border-white/10 text-xs text-slate-600 dark:text-slate-300">
                      {t.topic.length > 46 ? `${t.topic.slice(0, 46)}…` : t.topic}
                      <span className="ml-1 text-gray-400">{t.count}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-2">
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Recent statements
              </div>
              {profile.statements.length === 0 ? (
                <p className="text-sm text-gray-400">Nothing in this window.</p>
              ) : profile.statements.map((s) => (
                <div key={s.id} className="p-3 rounded-lg bg-gray-50/60 dark:bg-[#0d1424]/40 border border-slate-200/50 dark:border-white/5">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className={cn("text-[11px] font-semibold", SENTIMENT_STYLE[s.sentiment] || SENTIMENT_STYLE.neutral)}>
                      {s.sentiment}
                    </span>
                    {s.date && <span className="text-[10px] text-gray-400">{s.date}</span>}
                    {s.source_name && <span className="text-[10px] text-gray-400">{s.source_name}</span>}
                    {s.source_scope === "fr" && (
                      <span className="text-[10px] px-1.5 rounded bg-pharma-blue/10 text-pharma-blue dark:text-blue-300">FR</span>
                    )}
                    {s.url && (
                      <a href={s.url} target="_blank" rel="noreferrer"
                        className="text-gray-400 hover:text-pharma-blue"><ExternalLink size={11} /></a>
                    )}
                  </div>
                  {s.topic && <p className="text-xs font-medium text-gray-600 dark:text-[#94a3b8]">{s.topic}</p>}
                  <p className="text-sm text-gray-700 dark:text-[#e2e8f0] mt-0.5">{s.what_they_said}</p>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ProfileCard({ profile, onOpen }: { profile: KolProfileCard; onOpen: () => void }) {
  return (
    <button onClick={onOpen}
      className="text-left glass rounded-xl p-4 hover:border-pharma-blue/40 border border-transparent transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className={cn("font-semibold text-sm truncate", !profile.active && "text-slate-400")}>
            {profile.name}
          </h3>
          <p className="text-[11px] text-gray-400">
            {profile.insight_count} statements
            {profile.last_activity && ` · last ${new Date(profile.last_activity).toLocaleDateString()}`}
            {!profile.active && " · inactive"}
          </p>
        </div>
        <TrendingUp size={14} className="text-gray-300 shrink-0" />
      </div>
      {profile.summary_bullets.length > 0 ? (
        <p className="text-xs text-gray-600 dark:text-[#94a3b8] mt-2 line-clamp-3">
          {profile.summary_bullets[0]}
        </p>
      ) : (
        <p className="text-xs text-gray-400 mt-2">No summary yet.</p>
      )}
    </button>
  );
}

export default function KolModule() {
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [type, setType] = useState<"kol" | "competitor">("kol");

  const { data, isLoading } = useQuery({
    queryKey: ["kol-profiles", type],
    queryFn: () => api.kolProfiles(undefined, type),
  });

  // Filtering client-side keeps typing instant; the endpoint also accepts `q`
  // for when the roster outgrows one page.
  const profiles = (data?.profiles ?? []).filter((p) =>
    p.name.toLowerCase().includes(query.trim().toLowerCase()));
  const withSummary = profiles.filter((p) => p.summary_bullets.length > 0).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Users size={22} className="text-pharma-blue" />
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">KOL Module</h1>
        </div>
        <div className="inline-flex rounded-lg border border-slate-200 dark:border-white/10 p-0.5">
          {(["kol", "competitor"] as const).map((t) => (
            <button key={t} onClick={() => setType(t)}
              className={cn("px-3 py-1.5 text-xs rounded-md font-medium",
                type === t ? "bg-pharma-blue text-white" : "text-slate-500")}>
              {t === "kol" ? "KOLs" : "Competitors"}
            </button>
          ))}
        </div>
      </div>

      <ShareOfVoice />

      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name…"
          className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent text-sm"
        />
      </div>

      <p className="text-xs text-gray-400">
        {profiles.length} tracked · {withSummary} with a stored summary
      </p>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />Loading…
        </p>
      ) : profiles.length === 0 ? (
        <p className="text-sm text-gray-400">No match.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {profiles.map((p) => (
            <ProfileCard key={p.id} profile={p} onOpen={() => setOpenId(p.id)} />
          ))}
        </div>
      )}

      {openId != null && <ProfileDetail id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}