import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown, ChevronUp, Compass, ExternalLink, Linkedin, Loader2, Search,
  Sparkles, TrendingUp, Twitter, Users, X,
} from "lucide-react";
import { api, type KolProfileCard, type KolResearch } from "@/lib/api";
import TransparencePanel from "@/components/TransparencePanel";
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

/** The publication and trial record.
 *
 *  Every figure here is computed from registry metadata, so it can be shown as
 *  a number rather than hedged prose. The collaborator list is the part with
 *  the most leverage: it answers "who speaks on this topic that we do not
 *  already follow" directly, from who the KOL actually publishes with.
 */
/** Writes a KOL's synthesis on demand.
 *
 *  Summaries used to be produced only as a step inside a scrape run, so a KOL
 *  whose insights arrived from publications showed "No summary yet" while
 *  sitting on dozens of statements. Generation runs in a worker, so the panel
 *  polls the profile until the summary lands rather than guessing a delay. */
/** Who leads this topic in France that we do NOT already follow.
 *
 *  The spec asks for "the main speaker for topic X or Y that could be outside
 *  our current audience". Every other view can only describe the people already
 *  on the target list; this finds the list itself, ranked by publication volume
 *  rather than by who happens to post on social media.
 *
 *  Measured on lung cancer: four of the ten most prolific French authors were
 *  untracked, including one with 73k citations.
 */
function KolDiscovery() {
  const [topic, setTopic] = useState("lung cancer");
  const [submitted, setSubmitted] = useState("lung cancer");
  const [open, setOpen] = useState(false);

  const { data, isFetching } = useQuery({
    queryKey: ["kol-discovery", submitted],
    queryFn: () => api.discoverKols(submitted),
    enabled: open,
    staleTime: 10 * 60 * 1000,
  });

  return (
    <div className="glass rounded-xl p-4 space-y-3">
      <button onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full gap-3 text-left">
        <div className="flex items-center gap-2 min-w-0">
          <Compass size={16} className="text-pharma-blue dark:text-blue-300 shrink-0" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-gray-800 dark:text-[#e2e8f0]">
              Who else leads this topic
            </h2>
            <p className="text-xs text-gray-400">
              Most published French authors, and which we already follow
            </p>
          </div>
        </div>
        {open ? <ChevronUp size={16} className="text-gray-400" />
              : <ChevronDown size={16} className="text-gray-400" />}
      </button>

      {open && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <input value={topic} onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && setSubmitted(topic.trim() || "lung cancer")}
              placeholder="lung cancer"
              className="flex-1 px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
            <button onClick={() => setSubmitted(topic.trim() || "lung cancer")}
              className="px-3 py-1.5 text-xs bg-pharma-blue text-white rounded-lg hover:bg-pharma-light transition-colors">
              Search
            </button>
          </div>

          {isFetching ? (
            <p className="flex items-center gap-2 text-sm text-gray-400">
              <Loader2 size={14} className="animate-spin" /> Ranking authors…
            </p>
          ) : !data?.candidates?.length ? (
            <p className="text-sm text-gray-400">
              No untracked authors found — everyone leading this topic is already followed.
            </p>
          ) : (
            <>
              <p className="text-[11px] text-gray-400">
                {data.tracked_count} of the top authors are already tracked.
                The rest are candidates to add.
              </p>
              <div className="space-y-1.5">
                {data.candidates.slice(0, 10).map((a) => (
                  <div key={a.openalex_id}
                    className="flex items-start gap-3 p-2.5 rounded-lg border border-slate-200/60 dark:border-white/5">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0] truncate">
                        {a.name}
                        {/* France-based is what the client is buying; a foreign
                            co-author of French research is real signal but must
                            not be mistaken for local coverage. */}
                        {!a.france_based && a.institution_country && (
                          <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-gray-500 uppercase">
                            {a.institution_country}
                          </span>
                        )}
                      </p>
                      <p className="text-[11px] text-gray-400 truncate">
                        {a.institution || "institution unknown"}
                        {a.cited_by_count ? ` · ${a.cited_by_count.toLocaleString()} citations` : ""}
                      </p>
                      {a.topics && a.topics.length > 0 && (
                        <p className="text-[10px] text-gray-400 truncate mt-0.5">{a.topics[0]}</p>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-gray-800 dark:text-white tabular-nums">
                        {a.papers_on_topic}
                      </p>
                      <p className="text-[10px] text-gray-400">papers</p>
                    </div>
                    {/* Router Link, not a raw href: an <a> forces a full page
                        reload in an SPA. Targets reads ?add= / ?type= on mount
                        and prefills its form. */}
                    <Link to={`/targets?add=${encodeURIComponent(a.name)}&type=kol`}
                      title="Add as a tracked KOL"
                      className="shrink-0 self-center px-2 py-1 text-[11px] border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5">
                      Track
                    </Link>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}


/**
 * Purely presentational — the "is this generating" state and the poll that
 * watches for it to finish both live on the page component, not here.
 *
 * This used to own both: its own `waiting` state and its own 3s poll. That
 * meant closing the detail panel after clicking Generate — or switching to a
 * different KOL and back — unmounted this component, killing the poll. The
 * backend task kept running (a Celery task doesn't know or care that the tab
 * moved on), but nothing was left watching for it to land, so reopening the
 * KOL later showed whatever had been fetched at that moment — "no summary" if
 * you reopened before the task finished — with nothing to refresh it after.
 * Multiple KOLs generated in a row this way could ALL look like they failed,
 * even though every one of them wrote a summary server-side.
 */
function SynthesisButton({ hasSummary, generating, error, onGenerate }: {
  hasSummary: boolean; generating: boolean; error: string | null;
  onGenerate: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-[11px] text-red-400">{error.slice(0, 80)}</span>}
      <button onClick={onGenerate} disabled={generating}
        className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 disabled:opacity-50 transition-colors">
        {generating
          ? <Loader2 size={11} className="animate-spin" />
          : <Sparkles size={11} />}
        {generating ? "Writing…" : hasSummary ? "Regenerate" : "Generate"}
      </button>
    </div>
  );
}


function ResearchRecord({ research }: { research: KolResearch }) {
  const hasRecord = research.publication_count > 0 || research.trial_count > 0;
  if (!hasRecord) {
    return (
      <p className="text-sm text-gray-400">
        No publications or trials collected yet — these arrive from Europe PMC and
        ClinicalTrials.gov on the nightly sync.
      </p>
    );
  }

  const stats = [
    { label: "Publications", value: research.publication_count },
    { label: "Citations", value: research.total_citations },
    { label: "Open access", value: research.open_access_count },
    { label: "Trials", value: research.trial_count },
  ];
  const untracked = research.collaborators.filter((c) => !c.tracked);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {stats.map((s) => (
          <div key={s.label} className="p-2.5 rounded-lg bg-slate-50/60 dark:bg-white/5">
            <p className="text-lg font-bold text-gray-800 dark:text-white tabular-nums">{s.value}</p>
            <p className="text-[10px] text-gray-400">{s.label}</p>
          </div>
        ))}
      </div>

      {research.top_journals.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Where they publish
          </div>
          <div className="flex flex-wrap gap-1.5">
            {research.top_journals.map((j) => (
              <span key={j.journal}
                className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-gray-600 dark:text-[#94a3b8]">
                {j.journal} <span className="tabular-nums text-gray-400">×{j.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {research.collaborators.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              Who they publish with
            </div>
            {untracked.length > 0 && (
              <span className="text-[10px] text-amber-600 dark:text-amber-400">
                {untracked.length} outside the tracked list
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {research.collaborators.map((c) => (
              <span key={c.name}
                title={c.tracked ? "Already tracked" : "Not tracked — a candidate to add"}
                className={cn("text-[11px] px-2 py-0.5 rounded-full",
                  c.tracked
                    ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300")}>
                {c.name} <span className="tabular-nums opacity-70">×{c.papers}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {research.publications.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Recent publications
          </div>
          <div className="space-y-1.5">
            {research.publications.slice(0, 8).map((pub) => (
              <a key={pub.url} href={pub.url} target="_blank" rel="noreferrer"
                className="block p-2.5 rounded-lg border border-slate-200/60 dark:border-white/5 hover:border-pharma-blue/40 transition-colors">
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-snug">{pub.title}</p>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-gray-400 flex-wrap">
                  {pub.journal && <span className="font-medium">{pub.journal}</span>}
                  {pub.date && <span>· {pub.date.slice(0, 10)}</span>}
                  {pub.cited_by > 0 && <span>· cited {pub.cited_by}×</span>}
                  {pub.open_access && (
                    <span className="text-emerald-600 dark:text-emerald-400">· open access</span>
                  )}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      {research.trials.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Registered trials
          </div>
          <div className="space-y-1.5">
            {research.trials.slice(0, 6).map((t) => (
              <a key={t.url} href={t.url} target="_blank" rel="noreferrer"
                className="block p-2.5 rounded-lg border border-slate-200/60 dark:border-white/5 hover:border-pharma-blue/40 transition-colors">
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-snug">{t.title}</p>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-gray-400 flex-wrap">
                  {t.nct_id && <span className="font-medium">{t.nct_id}</span>}
                  {t.phase && <span>· {t.phase}</span>}
                  {t.status && <span>· {t.status.replace(/_/g, " ").toLowerCase()}</span>}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


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

function ProfileDetail({ id, onClose, generating, generateError, onGenerate }: {
  id: number; onClose: () => void;
  generating: boolean; generateError: string | null; onGenerate: () => void;
}) {
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
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Summary of what they said
                  </div>
                  <SynthesisButton hasSummary generating={generating}
                    error={generateError} onGenerate={onGenerate} />
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
              <div className="space-y-2">
                <p className="text-sm text-gray-400">
                  No synthesis yet — press Generate to write one from the
                  {" "}{profile.insight_count} statements collected.
                </p>
                <SynthesisButton hasSummary={false} generating={generating}
                  error={generateError} onGenerate={onGenerate} />
              </div>
            )}

            {profile.so_what && (
              <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
                <div className="text-[10px] font-semibold text-amber-700 dark:text-amber-300 uppercase tracking-wider mb-1">
                  So what for pharma
                </div>
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] whitespace-pre-wrap">{profile.so_what}</p>
              </div>
            )}

            {profile.research && (
              <div className="space-y-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Research record
                </div>
                <ResearchRecord research={profile.research} />
              </div>
            )}

            {/* Declared industry payments. Sits under the research record because
                both answer "what is this person's standing in the field" from
                external evidence rather than from what they posted. */}
            <TransparencePanel targetId={id} />

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

// sessionStorage helpers, matching the pattern in AccountTracking.tsx: the
// generation state has to survive this page unmounting (a route change),
// not just a detail panel closing. Restored state is still verified against
// the server on the next poll, never trusted blindly.
function loadPersisted<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch { return fallback; }
}
function persist(key: string, value: unknown) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode etc. */ }
}

export default function KolModule() {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [type, setType] = useState<"kol" | "competitor">("kol");

  const { data, isLoading } = useQuery({
    queryKey: ["kol-profiles", type],
    queryFn: () => api.kolProfiles(undefined, type),
  });

  // Profiles currently generating a summary, mapped to the timestamp they had
  // when generation started — this, not a plain boolean, is what "on the page
  // level" means here: the SAME polling loop keeps working whether the detail
  // panel for that KOL is open, closed, or was closed and a different one
  // opened in between.
  const [generating, setGenerating] = useState<Record<number, string | null>>(
    () => loadPersisted("kol:generating", {}));
  useEffect(() => persist("kol:generating", generating), [generating]);
  const [generateErrors, setGenerateErrors] = useState<Record<number, string>>({});
  const generatingIds = Object.keys(generating).map(Number);

  useQuery({
    queryKey: ["kol-generate-watch", generatingIds.join(",")],
    queryFn: async () => {
      const fresh = await api.kolProfiles(undefined, type);
      qc.setQueryData(["kol-profiles", type], fresh);
      const stillGenerating: Record<number, string | null> = {};
      for (const [key, since] of Object.entries(generating)) {
        const id = Number(key);
        const row = fresh.profiles.find((p) => p.id === id);
        if (row && row.summary_generated_at !== since) {
          qc.invalidateQueries({ queryKey: ["kol-profile", id] });
        } else {
          stillGenerating[id] = since;
        }
      }
      if (Object.keys(stillGenerating).length !== generatingIds.length) {
        setGenerating(stillGenerating);
      }
      return fresh;
    },
    enabled: generatingIds.length > 0,
    refetchInterval: 3000,
  });

  // Safety net: a dead worker or an unregistered task must not leave the
  // button stuck on "Writing…" forever.
  useEffect(() => {
    if (!generatingIds.length) return;
    const timer = setTimeout(() => setGenerating({}), 120_000);
    return () => clearTimeout(timer);
  }, [generatingIds.length]);

  const generateMut = useMutation({
    mutationFn: (id: number) => api.regenerateKolSummary(id),
    onMutate: (id) => {
      setGenerateErrors((e) => { const n = { ...e }; delete n[id]; return n; });
      const current = (data?.profiles ?? []).find((p) => p.id === id);
      setGenerating((g) => ({ ...g, [id]: current?.summary_generated_at ?? null }));
    },
    onError: (err, id) => {
      setGenerating((g) => { const n = { ...g }; delete n[id]; return n; });
      setGenerateErrors((e) => ({ ...e, [id]: (err as Error)?.message || "Failed" }));
    },
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

      {/* Stakeholder identification: who leads this topic that we don't follow. */}
      <KolDiscovery />

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

      {openId != null && (
        <ProfileDetail id={openId} onClose={() => setOpenId(null)}
          generating={openId in generating}
          generateError={generateErrors[openId] ?? null}
          onGenerate={() => generateMut.mutate(openId)} />
      )}
    </div>
  );
}