import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect, useMemo, useRef } from "react";
import {
  Flame, Heart, MessageCircle, Eye, Share2, ExternalLink, X,
  Sparkles, Loader2, Search, RefreshCw, SlidersHorizontal, Clock,
} from "lucide-react";
import MarketResearchReport from "@/components/MarketResearchReport";
import { api, type SocialPost } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";
import { useHiddenRecents } from "@/lib/hiddenRecents";
import { SynthesisPanel } from "@/components/SynthesisPanel";
import { useGenQuota } from "@/hooks/useGenQuota";

const PLATFORM_COLOR: Record<string, string> = {
  instagram: "bg-pink-100 text-pink-700 dark:bg-pink-900/20 dark:text-pink-300",
  twitter:   "bg-sky-100 text-sky-700 dark:bg-sky-900/20 dark:text-sky-300",
  linkedin:  "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200",
  facebook:  "bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300",
};

const SORT_OPTIONS = [
  { value: "trending",  label: "Trending" },
  { value: "likes",     label: "Most Liked" },
  { value: "comments",  label: "Most Comments" },
  { value: "recent",    label: "Most Recent" },
];

const PLATFORMS = [
  { value: "all",       label: "All" },
  { value: "instagram", label: "Instagram" },
  { value: "twitter",   label: "X / Twitter" },
  { value: "linkedin",  label: "LinkedIn" },
  { value: "facebook",  label: "Facebook" },
];

const DATE_RANGES = [
  { value: 7,   label: "Last 7 days" },
  { value: 30,  label: "Last 30 days" },
  { value: 90,  label: "Last 3 months" },
  { value: 180, label: "All time" },
];

const KIND_OPTIONS = [
  { value: "all",     label: "All posts" },
  { value: "field",   label: "Medical field" },
  { value: "kol",     label: "KOLs" },
  // The account lanes tag their rows kind="account" and the API already filters
  // on it, but there was no way to ask for them — so posts collected from the
  // accounts the client chose to track were indistinguishable from keyword luck.
  { value: "account", label: "Tracked accounts" },
  { value: "comment", label: "Comments" },
];

const MIN_LIKES_OPTIONS = [
  { value: 0,    label: "Any" },
  { value: 10,   label: "10+" },
  { value: 100,  label: "100+" },
  { value: 1000, label: "1,000+" },
];

// Defaults — used for "reset" detection and actual reset
const LANGUAGE_OPTIONS = [
  { value: "all", label: "Global (all)" },
  { value: "fr",  label: "France only" },
  { value: "en",  label: "English only" },
];

const DEFAULTS = { sortBy: "trending", platform: "all", days: 30, kind: "all", minLikes: 0, language: "fr", fromDate: "", toDate: "" };

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

function Stat({ icon: Icon, value }: { icon: React.ElementType; value: number }) {
  if (!value) return null;
  return (
    <span className="flex items-center gap-1 text-[11px] text-gray-500 dark:text-[#94a3b8]">
      <Icon size={11} /> {fmt(value)}
    </span>
  );
}

function FilterSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-1.5 px-2">{title}</p>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function FilterBtn({
  active, onClick, children, badge,
}: {
  active: boolean; onClick: () => void; children: React.ReactNode; badge?: number | string;
}) {
  return (
    <button onClick={onClick}
      className={cn(
        "w-full text-left text-xs py-1.5 px-2 rounded-lg transition-colors flex items-center justify-between gap-2",
        active
          ? "bg-pharma-blue/10 text-pharma-blue dark:bg-pharma-blue/20 dark:text-blue-300 font-semibold"
          : "text-gray-600 dark:text-[#94a3b8] hover:bg-gray-100/70 dark:hover:bg-slate-800/60 hover:text-gray-900 dark:hover:text-white"
      )}>
      <span>{children}</span>
      {badge !== undefined && badge !== "" && (
        <span className="text-[10px] text-gray-400 dark:text-slate-500 tabular-nums shrink-0">{badge}</span>
      )}
    </button>
  );
}

export default function SocialTrends() {
  const [sortBy, setSortBy]       = useState(DEFAULTS.sortBy);
  const [platform, setPlatform]   = useState(DEFAULTS.platform);
  const [days, setDays]           = useState(DEFAULTS.days);
  const [kind, setKind]           = useState(DEFAULTS.kind);
  const [minLikes, setMinLikes]   = useState(DEFAULTS.minLikes);
  const { socialLang: language, setSocialLang: setLanguage } = useAppStore();
  const [fromDate, setFromDate]   = useState(DEFAULTS.fromDate);
  const [toDate, setToDate]       = useState(DEFAULTS.toDate);
  const [topicFilter, setTopicFilter] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selected, setSelected]   = useState<SocialPost | null>(null);

  // Manual search
  const [query, setQuery]         = useState("");
  const [submitted, setSubmitted] = useState("");
  const [searching, setSearching] = useState(false);
  const [polls, setPolls]         = useState(0);

  const qc = useQueryClient();

  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });
  const apifyOn = !!settings?.apify_configured;

  // One generous fetch per language scope — days/platform/kind filtering stays
  // client-side for instant UX. Language is server-side because the API is
  // France-first by default; it is in the query key so switching to Global
  // refetches the wider pool instead of filtering an already-French one.
  const { data: allData } = useQuery({
    queryKey: ["social-trends-all", language],
    queryFn: () => api.social.trends(180, "all", "all", 500, language),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const { data: status } = useQuery({
    queryKey: ["social-status"],
    queryFn: api.social.status,
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 30_000),
  });

  // Recent discover queries (sidebar) — UI hide list only, data stays cached
  const { data: discHist } = useQuery({
    queryKey: ["social-disc-hist"],
    queryFn: api.social.discoverHistory,
    staleTime: 30_000,
  });
  const { hide: hideRecent, isHidden: isRecentHidden } = useHiddenRecents("social-hidden-recents");
  const visibleRecents = useMemo(
    () => (discHist?.queries ?? []).filter(h => !isRecentHidden(h.query)),
    [discHist, isRecentHidden]
  );

  // Tracks when the search was fired so we know how long we've been waiting
  const searchStartedAt = useRef<number>(0);
  // True once we've seen discoverStatus.running === true (confirms the Celery task started)
  const seenRunning = useRef(false);

  // Manual search
  const { data: searchData } = useQuery({
    queryKey: ["social-search", submitted, polls, language],
    queryFn: () => api.social.discover(submitted, false, language),
    enabled: submitted.length > 1,
  });
  const searchMut = useMutation({
    mutationFn: () => api.social.discover(submitted, true, language),
    // onMutate (not onSuccess): setSubmitted() enables the searchData query
    // immediately, which returns an empty cache hit in ~50ms. If `searching` is
    // still false at that moment the empty state renders "No posts found" for the
    // few seconds the trigger POST is in flight, then flips to "Pulling posts…".
    // Setting the flag synchronously before the request closes that window.
    onMutate: () => {
      setPolls(0);
      setSearching(true);
      searchStartedAt.current = Date.now();
      seenRunning.current = false;
    },
    // Without this a failed trigger would spin until the 2-min hard cap.
    onError: () => setSearching(false),
  });

  // Poll discover status — this tells us when Apify actually finishes
  const { data: discoverStatus } = useQuery({
    queryKey: ["social-discover-status", submitted],
    queryFn: () => api.social.discoverStatus(submitted),
    enabled: searching && submitted.length > 1,
    refetchInterval: searching ? 3000 : false,
  });
  // Terms the search actually matched on. The read path expands the question
  // itself now, so this no longer waits for a live scrape to report back —
  // which never happened at all when Apify was out of quota.
  const expandedTerms = searchData?.terms?.length
    ? searchData.terms
    : (discoverStatus?.terms ?? []);

  // Drive searching state from discoverStatus.running, not a fixed poll count.
  // "Done" only when we've confirmed the task started (seenRunning OR >15s elapsed)
  // AND discoverStatus says running=false. Safety cap at 3 minutes.
  useEffect(() => {
    if (!searching) return;

    const elapsed = Date.now() - searchStartedAt.current;

    if (discoverStatus?.running === true) {
      seenRunning.current = true;
    }

    const taskConfirmedStarted = seenRunning.current || elapsed > 15_000;
    if (taskConfirmedStarted && discoverStatus?.running === false) {
      setSearching(false);
      setPolls(p => p + 1); // one final cache refresh to pick up newly inserted posts
      qc.invalidateQueries({ queryKey: ["social-trends-all"] });
      return;
    }

    if (elapsed > 120_000) { // 2 min hard cap
      setSearching(false);
      return;
    }

    // Keep re-fetching cached results every 5s so posts trickle in as they're inserted
    const t = setTimeout(() => setPolls(p => p + 1), 5000);
    return () => clearTimeout(t);
  }, [searching, polls, discoverStatus]);

  function runSearch() {
    const q = query.trim();
    if (q.length < 2) return;
    setSubmitted(q);
    if (apifyOn) searchMut.mutate();
  }
  function clearSearch() { setSubmitted(""); setQuery(""); setSearching(false); setPolls(0); }
  function resetFilters() { setSortBy(DEFAULTS.sortBy); setPlatform(DEFAULTS.platform); setDays(DEFAULTS.days); setKind(DEFAULTS.kind); setMinLikes(DEFAULTS.minLikes); setLanguage(DEFAULTS.language); setFromDate(DEFAULTS.fromDate); setToDate(DEFAULTS.toDate); setTopicFilter(null); }

  const allPosts = allData?.top_posts ?? [];

  // ISO cutoff string — ISO strings sort lexicographically, so >= comparison works
  const cutoff = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString();
  }, [days]);

  // Client-side filter + sort (instant, no extra fetches)
  const filtered = useMemo(() => {
    let posts = [...allPosts];
    if (platform !== "all")  posts = posts.filter(p => p.platform === platform);
    if (kind !== "all")      posts = posts.filter(p => p.kind === kind);
    if (minLikes > 0)        posts = posts.filter(p => (p.likes ?? 0) >= minLikes);
    if (topicFilter)         posts = posts.filter(p => p.topic === topicFilter);
    if (language !== "all")  posts = posts.filter(p => p.language === language);
    if (fromDate)            posts = posts.filter(p => p.posted_at && p.posted_at >= fromDate);
    if (toDate)              posts = posts.filter(p => p.posted_at && p.posted_at <= toDate + "T23:59:59Z");
    // Date filter on posted_at; skip posts with no date
    posts = posts.filter(p => !p.posted_at || p.posted_at >= cutoff);

    if (sortBy === "likes")    posts.sort((a, b) => (b.likes ?? 0) - (a.likes ?? 0));
    else if (sortBy === "comments") posts.sort((a, b) => (b.comments ?? 0) - (a.comments ?? 0));
    else if (sortBy === "recent")   posts.sort((a, b) => (b.posted_at ?? "").localeCompare(a.posted_at ?? ""));
    // "trending" is already sorted by trend_score from API

    return posts;
  }, [allPosts, platform, kind, minLikes, topicFilter, language, fromDate, toDate, cutoff, sortBy]);

  // Platform counts on the current non-platform-filtered set (shows how many each platform has)
  const platformCounts = useMemo(() => {
    let base = [...allPosts];
    if (kind !== "all")      base = base.filter(p => p.kind === kind);
    if (minLikes > 0)        base = base.filter(p => (p.likes ?? 0) >= minLikes);
    if (language !== "all")  base = base.filter(p => p.language === language);
    if (fromDate)            base = base.filter(p => p.posted_at && p.posted_at >= fromDate);
    if (toDate)              base = base.filter(p => p.posted_at && p.posted_at <= toDate + "T23:59:59Z");
    base = base.filter(p => !p.posted_at || p.posted_at >= cutoff);
    const c: Record<string, number> = { all: base.length };
    for (const p of base) c[p.platform] = (c[p.platform] ?? 0) + 1;
    return c;
  }, [allPosts, kind, minLikes, language, fromDate, toDate, cutoff]);

  // On-demand AI synthesis over the WHOLE DB (KOL + social) — always produces output
  const { can: canGen, spent: genSpent } = useGenQuota();
  const synthMut = useMutation({
    mutationFn: (refresh: boolean) => api.combinedSynthesis(refresh),
    onSuccess: () => genSpent(),
  });
  const synth = synthMut.data;

  // Most impactful posts by raw engagement (free, client-side) — whole feed
  const topImpact = useMemo(() => {
    const eng = (p: SocialPost) => (p.likes ?? 0) + 2 * (p.comments ?? 0) + 1.5 * (p.shares ?? 0);
    return [...allPosts].sort((a, b) => eng(b) - eng(a)).slice(0, 4);
  }, [allPosts]);

  const searchPosts = searchData?.results ?? [];
  const isDefault = sortBy === DEFAULTS.sortBy && platform === DEFAULTS.platform &&
    days === DEFAULTS.days && kind === DEFAULTS.kind && minLikes === DEFAULTS.minLikes &&
    language === DEFAULTS.language && !fromDate && !toDate && !topicFilter;

  return (
    <div className="glass rounded-xl flex flex-col h-full overflow-hidden">

      {/* ── Header ── */}
      <div className="flex flex-wrap items-center gap-3 px-5 pt-5 pb-4 border-b border-slate-200/50 dark:border-slate-800/50 flex-none">
        <button onClick={() => setSidebarOpen(o => !o)}
          title={sidebarOpen ? "Hide filters" : "Show filters"}
          className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-slate-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors shrink-0">
          <SlidersHorizontal size={16} />
        </button>

        <div className="flex items-center gap-2.5 shrink-0">
          <div className="p-2 bg-orange-50 dark:bg-orange-900/20 rounded-lg">
            <Flame size={16} className="text-orange-500" />
          </div>
          <div>
            <h2 className="font-semibold text-sm whitespace-nowrap">Trending on Social</h2>
            <p className="text-[11px] text-gray-400">
              Instagram · X · LinkedIn · Facebook
              {filtered.length > 0 && <span className="ml-1">— {filtered.length} posts</span>}
            </p>
          </div>
        </div>

        {/* Search */}
        <div className="flex gap-2 flex-1 min-w-0">
          <div className="relative flex-1 min-w-0">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && runSearch()}
              placeholder="Search a topic, drug or competitor…"
              className="w-full h-9 pl-8 pr-3 rounded-lg border border-gray-200 dark:border-[#1e3a5f] bg-gray-50 dark:bg-[#111827] text-sm text-gray-900 dark:text-[#e2e8f0] focus:outline-none focus:ring-2 focus:ring-orange-400/30" />
          </div>
          <button onClick={runSearch} disabled={query.trim().length < 2 || searching}
            title={apifyOn ? "Search via Apify across all platforms" : "Set APIFY_API_TOKEN to pull fresh results"}
            className="h-9 px-4 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-sm font-semibold rounded-lg flex items-center gap-1.5 shrink-0">
            {searching ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
            Search
          </button>
          {submitted && (
            <button onClick={clearSearch} className="h-9 px-3 text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Scan status banner */}
      {status?.running && (
        <div className="flex items-center gap-2 px-5 py-2 text-xs text-blue-700 dark:text-blue-300 bg-blue-50/60 dark:bg-blue-900/10 border-b border-blue-100 dark:border-blue-900/30 flex-none">
          <Loader2 size={13} className="animate-spin" />
          Scanning social platforms… {status.done ?? 0}/{status.total ?? 0} jobs · {status.inserted ?? 0} posts found
        </div>
      )}

      {/* ── Body: sidebar + main ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Left sidebar */}
        {sidebarOpen && (
          <div className="w-48 shrink-0 border-r border-slate-200/50 dark:border-slate-800/50 p-3 flex flex-col gap-4 overflow-y-auto">
            {!isDefault && (
              <button onClick={resetFilters}
                className="text-[11px] text-pharma-light hover:text-pharma-blue font-medium transition-colors text-left px-2">
                ← Reset filters
              </button>
            )}

            <FilterSection title="Sort by">
              {SORT_OPTIONS.map(o => (
                <FilterBtn key={o.value} active={sortBy === o.value} onClick={() => setSortBy(o.value)}>
                  {o.label}
                </FilterBtn>
              ))}
            </FilterSection>

            <FilterSection title="Platform">
              {PLATFORMS.map(p => (
                <FilterBtn key={p.value} active={platform === p.value} onClick={() => setPlatform(p.value)}
                  badge={platformCounts[p.value] ?? 0}>
                  {p.label}
                </FilterBtn>
              ))}
            </FilterSection>

            <FilterSection title="Date range">
              {DATE_RANGES.map(r => (
                <FilterBtn key={r.value} active={days === r.value && !fromDate && !toDate} onClick={() => { setDays(r.value); setFromDate(""); setToDate(""); }}>
                  {r.label}
                </FilterBtn>
              ))}
              <div className="px-2 pt-1 space-y-1">
                <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
                  className="w-full text-[10px] px-2 py-1 rounded border border-gray-200 dark:border-[#1e3a5f] bg-transparent text-gray-600 dark:text-gray-400" />
                <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
                  className="w-full text-[10px] px-2 py-1 rounded border border-gray-200 dark:border-[#1e3a5f] bg-transparent text-gray-600 dark:text-gray-400" />
              </div>
            </FilterSection>

            <FilterSection title="Language">
              {LANGUAGE_OPTIONS.map(o => (
                <FilterBtn key={o.value} active={language === o.value} onClick={() => setLanguage(o.value)}>
                  {o.label}
                </FilterBtn>
              ))}
            </FilterSection>

            <FilterSection title="Post type">
              {KIND_OPTIONS.map(o => (
                <FilterBtn key={o.value} active={kind === o.value} onClick={() => setKind(o.value)}>
                  {o.label}
                </FilterBtn>
              ))}
            </FilterSection>

            <FilterSection title="Min likes">
              {MIN_LIKES_OPTIONS.map(o => (
                <FilterBtn key={o.value} active={minLikes === o.value} onClick={() => setMinLikes(o.value)}>
                  {o.label}
                </FilterBtn>
              ))}
            </FilterSection>

            {visibleRecents.length > 0 && (
              <FilterSection title="Recent searches">
                {visibleRecents.slice(0,10).map(h => (
                  <div key={h.query}
                    className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs text-gray-500 dark:text-[#94a3b8] hover:bg-gray-100/70 dark:hover:bg-slate-800/60 transition-colors group">
                    <Clock size={9} className="shrink-0 opacity-50"/>
                    <button onClick={() => { setQuery(h.query); setSubmitted(h.query); }}
                      className="truncate flex-1 text-left hover:text-gray-900 dark:hover:text-white">
                      {h.query}
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); hideRecent(h.query); }}
                      title="Hide from recent (data stays cached for future searches)"
                      className="opacity-0 group-hover:opacity-100 hover:text-red-500 transition-opacity shrink-0 p-0.5">
                      <X size={10}/>
                    </button>
                  </div>
                ))}
              </FilterSection>
            )}
          </div>
        )}

        {/* Main content */}
        <div className="flex-1 min-w-0 overflow-y-auto">

          {/* Manual search results */}
          {submitted && (
            <div className="p-5 border-b border-slate-200/50 dark:border-slate-800/50">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  Results for "{submitted}"
                  {!searching && (
                    <span className="ml-2 text-gray-400 normal-case font-normal">{searchPosts.length} found</span>
                  )}
                </p>
                {searching && (
                  <span className="flex items-center gap-1.5 text-xs text-orange-500">
                    <RefreshCw size={11} className="animate-spin" /> Searching…
                  </span>
                )}
                {/* Show LLM-expanded terms once available */}
                {expandedTerms.length > 0 && (
                  <span className="flex items-center gap-1 flex-wrap">
                    <span className="text-[10px] text-gray-400">searching for:</span>
                    {expandedTerms.map(t => (
                      <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-pharma-blue/10 text-pharma-blue dark:bg-pharma-blue/20 dark:text-blue-300 font-medium">
                        {t}
                      </span>
                    ))}
                  </span>
                )}
              </div>
              {searchPosts.length === 0 ? (
                <p className="text-sm text-gray-400">
                  {searching ? "Pulling posts from social platforms…" : "No pharma-relevant posts found — try a more specific term."}
                </p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                  {searchPosts.map(p => <PostCard key={p.id} post={p} onClick={() => setSelected(p)} />)}
                </div>
              )}

              {/* A question deserves an answer, not just a list of posts. Same
                  component as Topic Explorer, so the format is identical: it
                  reuses an existing report for this question and only writes a
                  new one when there is none. Skipped for one-word lookups,
                  which are a filter rather than a question. */}
              {submitted.trim().split(/\s+/).length >= 3 && (
                <div className="mt-5">
                  <MarketResearchReport
                    question={submitted}
                    windowDays={30}
                    lang={language === "fr" ? "fr" : language}
                  />
                </div>
              )}
            </div>
          )}

          {filtered.length === 0 ? (
            <div className="text-center py-16 space-y-3 px-6">
              <Flame size={32} className="text-orange-200 dark:text-orange-900 mx-auto" />
              <p className="text-gray-500 dark:text-gray-400 text-sm">
                {status?.running
                  ? "Scan in progress — results will appear here shortly."
                  : allPosts.length === 0
                    ? "No social trends yet. Run a scan from Settings to populate this feed."
                    : "No posts match the current filters."}
              </p>
              {!isDefault && allPosts.length > 0 && (
                <button onClick={resetFilters}
                  className="text-xs text-pharma-light hover:text-pharma-blue font-medium transition-colors">
                  ← Reset filters
                </button>
              )}
            </div>
          ) : (
            <div className="p-5 space-y-5">
              {/* AI synthesis / takeaway */}
              <SynthesisPanel
                accent="orange"
                takeaway={synth?.takeaway}
                takeawayLabel="What's happening"
                soWhat={synth?.so_what}
                conclusion={synth?.conclusion}
                conclusionLabel="The bottom line"
                generatedAt={synth?.generated_at}
                cached={synth?.cached}
                error={synth?.error}
                isLoading={synthMut.isPending}
                isError={synthMut.isError}
                hasRun={!!synth}
                canRegenerate={canGen("synthesis")}
                onGenerate={() => synthMut.mutate(!!synth)}
                picks={synth?.focus && synth.focus.length > 0 ? (
                  <div>
                    <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
                      <Sparkles size={11} className="text-orange-500" /> What to focus on
                    </p>
                    <ul className="space-y-1.5">
                      {synth.focus.map((f, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-gray-700 dark:text-[#e2e8f0]">
                          <span className="w-1.5 h-1.5 rounded-full bg-orange-500 mt-1.5 shrink-0" />
                          <span>{f}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : undefined}
              />

              {/* Most impactful by engagement (free) */}
              {topImpact.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Most impactful — by engagement</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                    {topImpact.map(p => <PostCard key={p.id} post={p} onClick={() => setSelected(p)} />)}
                  </div>
                </div>
              )}

              {/* Trending topic chips */}
              {allData?.top_topics && allData.top_topics.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Trending topics</p>
                  <div className="flex flex-wrap gap-1.5">
                    {allData.top_topics.slice(0, 12).map((t) => {
                      const active = topicFilter === t.topic;
                      return (
                        <button key={t.topic}
                          onClick={() => setTopicFilter(active ? null : t.topic)}
                          className={cn(
                            "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors",
                            active
                              ? "bg-orange-500 text-white border-orange-500"
                              : "bg-orange-50 dark:bg-orange-900/15 text-orange-700 dark:text-orange-300 border-orange-100 dark:border-orange-900/30 hover:bg-orange-100 dark:hover:bg-orange-900/30"
                          )}>
                          <span className="font-medium">{t.topic}</span>
                          <span className={active ? "text-orange-100" : "text-orange-400"}>{t.count}</span>
                          <span className={cn("text-[10px]", active ? "text-orange-200" : "text-orange-400")}>· {fmt(t.engagement)}</span>
                          {active && <X size={10} className="ml-0.5" />}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Post grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {filtered.map((p) => <PostCard key={p.id} post={p} onClick={() => setSelected(p)} />)}
              </div>
            </div>
          )}
        </div>
      </div>

      {selected && <DescribeModal post={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

/* ── Post card ─────────────────────────────────────────────── */

function PostCard({ post: p, onClick }: { post: SocialPost; onClick: () => void }) {
  return (
    <div onClick={onClick}
      className="glass-panel rounded-xl overflow-hidden cursor-pointer hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 flex flex-col">
      {p.thumbnail_url && (
        <div className="h-32 bg-slate-100 dark:bg-slate-800 overflow-hidden shrink-0">
          <img src={p.thumbnail_url} alt="" loading="lazy" className="w-full h-full object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
        </div>
      )}
      <div className="p-3 flex-1 flex flex-col">
        <div className="flex items-center gap-2 mb-1.5">
          <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium capitalize", PLATFORM_COLOR[p.platform] ?? "bg-gray-100 text-gray-600")}>
            {p.platform}
          </span>
          {p.topic && <span className="text-[10px] text-gray-400 truncate">#{p.topic}</span>}
        </div>
        {p.author && <p className="text-xs font-semibold text-pharma-light truncate">{p.author}</p>}
        <p className="text-xs text-gray-600 dark:text-[#94a3b8] line-clamp-3 mt-1 flex-1">{p.text || "—"}</p>
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-gray-100 dark:border-slate-800">
          <Stat icon={Heart} value={p.likes} />
          <Stat icon={MessageCircle} value={p.comments} />
          <Stat icon={Eye} value={p.views} />
          <Stat icon={Share2} value={p.shares} />
        </div>
      </div>
    </div>
  );
}

/* ── Describe modal ────────────────────────────────────────── */

export function DescribeModal({ post, onClose }: { post: SocialPost; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["social-describe", post.id],
    queryFn: () => api.social.describe(post.id),
    staleTime: Infinity,
  });

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-md bg-white dark:bg-slate-900 shadow-2xl flex flex-col h-full overflow-y-auto">
        <div className="flex items-start justify-between p-5 border-b border-gray-100 dark:border-slate-800">
          <div className="min-w-0">
            <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium capitalize", PLATFORM_COLOR[post.platform] ?? "bg-gray-100 text-gray-600")}>
              {post.platform}
            </span>
            {post.author && <p className="text-sm font-semibold text-pharma-light mt-1.5 truncate">{post.author}</p>}
          </div>
          <button onClick={onClose} className="ml-4 text-gray-400 hover:text-gray-600 shrink-0"><X size={20} /></button>
        </div>

        <div className="p-5 space-y-5 flex-1">
          {post.thumbnail_url && (
            <img src={post.thumbnail_url} alt="" className="w-full rounded-lg object-cover max-h-64"
              onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
          )}

          {isLoading ? (
            <p className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={14} className="animate-spin" /> Analysing…</p>
          ) : isError ? (
            <p className="text-sm text-red-500">Couldn't generate a description.</p>
          ) : (
            <div className="space-y-3">
              <div>
                <p className="flex items-center gap-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">
                  <Sparkles size={12} className="text-pharma-light" /> What this is
                </p>
                <p className="text-sm text-gray-800 dark:text-[#e2e8f0] leading-relaxed">{data?.description}</p>
              </div>
              {data?.so_what && (
                <div className="rounded-lg bg-pharma-blue/5 dark:bg-[#2563eb]/10 border border-pharma-blue/20 dark:border-[#2563eb]/30 px-3 py-2.5">
                  <p className="text-xs font-semibold text-pharma-blue dark:text-[#93c5fd] uppercase tracking-wide mb-1">
                    So what for pharma?
                  </p>
                  <p className="text-sm text-gray-800 dark:text-[#e2e8f0] leading-relaxed">{data.so_what}</p>
                </div>
              )}
            </div>
          )}

          {post.text && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Post</p>
              <p className="text-sm text-gray-600 dark:text-[#94a3b8] leading-relaxed whitespace-pre-wrap">{post.text}</p>
            </div>
          )}

          <div className="flex items-center gap-4 pt-3 border-t border-gray-100 dark:border-slate-800">
            <Stat icon={Heart} value={post.likes} />
            <Stat icon={MessageCircle} value={post.comments} />
            <Stat icon={Eye} value={post.views} />
            <Stat icon={Share2} value={post.shares} />
          </div>

          {post.hashtags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {post.hashtags.slice(0, 12).map((h) => (
                <span key={h} className="text-[11px] text-gray-400">#{h}</span>
              ))}
            </div>
          )}

          <a href={post.post_url} target="_blank" rel="noreferrer"
            className="flex items-center justify-center gap-2 w-full py-2.5 border border-pharma-light text-pharma-light rounded-lg text-sm font-medium hover:bg-pharma-light hover:text-white transition-colors">
            <ExternalLink size={14} /> View Original Post
          </a>
        </div>
      </div>
    </div>
  );
}
