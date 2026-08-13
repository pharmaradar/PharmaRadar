import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useHiddenRecents } from "@/lib/hiddenRecents";
import {
  Search, RefreshCw, ExternalLink, Database, Globe,
  Youtube, FileText, Lock, AlertCircle, Play, X, BookOpen,
  Video, TrendingUp, Clock, Zap, Sparkles,
  ChevronDown, ChevronUp, History, Star, MessageCircle, Linkedin,
  ScanSearch, Link2, FlaskConical
} from "lucide-react";
import { api, type DiscoveryResult, type DiscoveryContent, type KolInsight, type SocialPost } from "@/lib/api";
import { cn } from "@/lib/utils";
import { DescribeModal as SocialDescribeModal } from "./SocialTrends";
import { SynthesisPanel } from "@/components/SynthesisPanel";
import { useGenQuota } from "@/hooks/useGenQuota";
import { Flame, Heart } from "lucide-react";
import MarketResearchReport from "@/components/MarketResearchReport";

/* ─── constants ──────────────────────────────────────────── */

const TRENDING = [
  "ASCO 2025", "alectinib NSCLC", "HER2 lung cancer",
  "CDK4/6 inhibitor", "atezolizumab", "osimertinib", "ESMO 2024", "pembrolizumab",
];

const SENT_STYLE: Record<string, string> = {
  positive: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-400 dark:border-emerald-800/30",
  negative: "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800/30",
  neutral:  "bg-gray-50 text-gray-600 border-gray-200 dark:bg-[#1e3a5f]/30 dark:text-[#94a3b8] dark:border-[#1e3a5f]",
};

const SENT_DOT: Record<string, string> = {
  positive: "bg-emerald-400", negative: "bg-red-400", neutral: "bg-gray-300 dark:bg-[#475569]",
};

function domainBg(domain: string): string {
  const known: Record<string, string> = {
    "nejm.org":"195 50% 20%","nature.com":"142 40% 18%","thelancet.com":"220 45% 22%",
    "ncbi.nlm.nih.gov":"200 48% 20%","pmc.ncbi.nlm.nih.gov":"200 48% 20%",
    "cancer.org":"340 45% 22%","asco.org":"220 60% 18%","esmo.org":"208 55% 20%",
    "fda.gov":"220 40% 20%","mdanderson.org":"0 50% 20%","merck.com":"215 45% 20%",
  };
  for (const [k,v] of Object.entries(known)) if (domain.includes(k)) return `hsl(${v})`;
  let h = 0;
  for (const c of domain) h = ((h<<5)-h)+c.charCodeAt(0);
  return `hsl(${Math.abs(h)%360}, 38%, 22%)`;
}

type SectionFilter = "all" | "kol-recent" | "kol-old" | "web-article" | "web-video" | "web-social";


function mediaLabel(type: string): string {
  if (type === "video") return "VIDEO";
  if (type === "linkedin") return "LINKEDIN";
  if (type === "twitter") return "X/TWITTER";
  if (type === "pdf") return "PDF";
  return "";
}

function mediaLabelClass(type: string): string {
  if (type === "video") return "bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400";
  if (type === "linkedin") return "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400";
  if (type === "twitter") return "bg-sky-50 text-sky-600 dark:bg-sky-900/20 dark:text-sky-400";
  if (type === "pdf") return "bg-orange-50 text-orange-500 dark:bg-orange-900/20 dark:text-orange-400";
  return "";
}

/* ─── main ───────────────────────────────────────────────── */

export default function TopicExplorer() {
  const [query, setQuery]         = useState("");
  const [submitted, setSubmitted] = useState("");
  const [filter, setFilter]       = useState<SectionFilter>("all");
  const [active, setActive]           = useState<DiscoveryResult | null>(null);
  const [describeResult, setDescribe] = useState<DiscoveryResult | null>(null);
  const [deepOpen, setDeepOpen]       = useState(false);
  const [langFilter, setLangFilter]   = useState("fr");
  const [fromDate, setFromDate]       = useState("");
  const [toDate, setToDate]           = useState("");

  const { data: history } = useQuery({ queryKey: ["disc-hist"], queryFn: api.discovery.history });
  const { hide: hideRecent, isHidden: isRecentHidden } = useHiddenRecents("discovery-hidden-recents");
  const visibleRecents = useMemo(
    () => (history?.queries ?? []).filter(h => !isRecentHidden(h.query)),
    [history, isRecentHidden]
  );

  const searchMut = useMutation({
    mutationFn: ({ q, refresh, lang }: { q: string; refresh: boolean; lang: string }) =>
      api.discovery.search(q, refresh, lang),
  });

  // On-demand AI synthesis of everything found for the current query
  const { can: canGen, spent: genSpent } = useGenQuota();
  const synthMut = useMutation({
    mutationFn: (refresh: boolean) => api.discovery.synthesis(submitted, langFilter, refresh),
    onSuccess: () => genSpent(),
  });
  const synth = synthMut.data;

  const { data: kolData, isLoading: kolLoading } = useQuery({
    queryKey: ["kol-ment", submitted],
    queryFn: () => api.discovery.kolMentions(submitted),
    enabled: submitted.length > 1,
  });

  // ── Social Trends (Apify) ──
  // Cached matches show for free on every search; an explicit button spends Apify
  // credits to pull fresh posts, then we poll a few times to surface them.
  const [socialActive, setSocialActive] = useState<SocialPost | null>(null);
  const [socialPolls, setSocialPolls] = useState(0);
  const [socialSearching, setSocialSearching] = useState(false);

  const { data: appSettings } = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });
  const apifyOn = !!appSettings?.apify_configured;

  // Cached social matches (no Apify cost). Re-keys on socialPolls so polling re-reads.
  const { data: socialCached } = useQuery({
    queryKey: ["social-disc", submitted, socialPolls],
    queryFn: () => api.social.discover(submitted, false),
    enabled: submitted.length > 1,
  });

  // Explicit "Search social" — triggers a live Apify pull for the current query.
  const socialSearchMut = useMutation({
    mutationFn: () => api.social.discover(submitted, true),
    onSuccess: () => { setSocialPolls(0); setSocialSearching(true); },
  });

  useEffect(() => { setSocialSearching(false); setSocialPolls(0); }, [submitted]);
  useEffect(() => {
    if (!socialSearching) return;
    if (socialPolls >= 6) { setSocialSearching(false); return; }
    const t = setTimeout(() => setSocialPolls(p => p + 1), 5000);
    return () => clearTimeout(t);
  }, [socialSearching, socialPolls]);

  const socialPosts = socialCached?.results ?? [];

  function run(q?: string, refresh = false) {
    const term = (q ?? query).trim();
    if (!term) return;
    setSubmitted(term); setQuery(term); setFilter("all");
    synthMut.reset();
    searchMut.mutate({ q: term, refresh, lang: langFilter });
  }

  const webRaw      = searchMut.data?.results ?? [];
  const fromCache   = searchMut.data?.from_cache ?? false;
  const webAll      = webRaw
    // France filters on the SOURCE, not the detected language of the snippet:
    // the backend already stores only French domains under the "fr" scope, so
    // re-testing the text here could only ever hide French sources that happen
    // to publish in English.
    .filter(r => langFilter === "all"
      || (langFilter === "fr" ? r.source_scope === "fr" : r.language === langFilter))
    .filter(r => !fromDate || !r.published_date || r.published_date >= fromDate)
    .filter(r => !toDate   || !r.published_date || r.published_date <= toDate);
  const webArticles = webAll.filter(r => r.media_type === "article" || r.media_type === "pdf" || r.media_type === "research");
  const webVideos   = webAll.filter(r => r.media_type === "video");
  const webSocial   = webAll.filter(r => r.media_type === "linkedin" || r.media_type === "twitter" || r.media_type === "social");
  const kolRecent   = kolData?.recent   ?? [];
  const kolOld      = kolData?.historical ?? [];
  const kolTotal    = kolData?.total ?? 0;

  const isLoading   = searchMut.isPending;

  const counts: Record<SectionFilter, number> = {
    "all": webAll.length + kolTotal,
    "kol-recent": kolRecent.length,
    "kol-old": kolOld.length,
    "web-article": webArticles.length,
    "web-video": webVideos.length,
    "web-social": webSocial.length,
  };

  const showKolRecent  = filter === "all" || filter === "kol-recent";
  const showKolOld     = filter === "all" || filter === "kol-old";
  const showWebArticle = filter === "all" || filter === "web-article";
  const showWebVideo   = filter === "all" || filter === "web-video";
  const showWebSocial  = filter === "all" || filter === "web-social";

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[#f8fafc] dark:bg-[#070c19]">

      {/* ══ HEADER ══ */}
      <div className="flex-none glass-panel border-b border-slate-200/50 dark:border-white/10 px-5 py-2.5 flex items-center gap-4 z-20 shadow-sm">
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-7 h-7 rounded-lg bg-pharma-blue flex items-center justify-center">
            <Globe size={14} className="text-white" />
          </div>
          <span className="font-bold text-sm text-gray-900 dark:text-white hidden sm:block">Discovery</span>
        </div>

        <div className="flex gap-2 flex-1 max-w-xl">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && run()}
              placeholder="Ask a question — e.g. What do doctors think about subcutaneous therapies in lung cancer?"
              className="w-full h-9 pl-8 pr-3 rounded-lg border border-gray-200 dark:border-[#1e3a5f] bg-gray-50 dark:bg-[#111827] text-sm text-gray-900 dark:text-[#e2e8f0] focus:outline-none focus:ring-2 focus:ring-pharma-blue/20 focus:border-pharma-blue transition-all" />
          </div>
          <button onClick={() => run()} disabled={isLoading || !query.trim()}
            className="h-9 px-4 bg-pharma-blue hover:bg-pharma-light disabled:opacity-40 text-white text-sm font-semibold rounded-lg flex items-center gap-1.5 transition-colors shrink-0">
            {isLoading ? <RefreshCw size={13} className="animate-spin" /> : <Search size={13} />}
            {isLoading ? "…" : "Search"}
          </button>
          {submitted && (
            <button onClick={() => setDeepOpen(true)}
              title="Deep Search — fetch everything from the internet about this topic"
              className="h-9 px-3 border border-pharma-blue/40 hover:border-pharma-blue text-pharma-blue hover:bg-pharma-blue/5 text-sm font-semibold rounded-lg flex items-center gap-1.5 transition-all shrink-0">
              <ScanSearch size={14}/><span className="hidden sm:block">Deep</span>
            </button>
          )}
          {submitted && (
            <button onClick={() => socialSearchMut.mutate()}
              disabled={!apifyOn || socialSearching || socialSearchMut.isPending}
              title={apifyOn ? "Search social media (Instagram, X, LinkedIn, Facebook) via Apify" : "Set APIFY_API_TOKEN to enable social search"}
              className="h-9 px-3 border border-orange-400/50 hover:border-orange-500 text-orange-600 dark:text-orange-400 hover:bg-orange-500/5 text-sm font-semibold rounded-lg flex items-center gap-1.5 transition-all shrink-0 disabled:opacity-40">
              {socialSearching
                ? <RefreshCw size={14} className="animate-spin"/>
                : <Flame size={14}/>}
              <span className="hidden sm:block">Social</span>
            </button>
          )}
        </div>

        {submitted && !isLoading && (
          <div className="ml-auto flex items-center gap-2 shrink-0">
            <span className="text-xs text-gray-400">{counts.all} results</span>
            {fromCache
              ? <Chip icon={<Database size={9}/>} label="Cached" cls="bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400"/>
              : <Chip icon={<Zap size={9}/>} label="Live" cls="bg-emerald-50 text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-400"/>}
            {fromCache && (
              <button onClick={() => run(submitted,true)} className="text-xs text-gray-400 hover:text-pharma-blue flex items-center gap-1 transition-colors">
                <RefreshCw size={10}/>Refresh
              </button>
            )}
          </div>
        )}
      </div>

      {/* ══ BODY ══ */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* ── Sidebar ── */}
        <div className="hidden lg:flex flex-col w-44 flex-none glass-panel border-r border-slate-200/50 dark:border-white/10 overflow-y-auto">
          <div className="p-3 space-y-0.5">
            {submitted && (
              <>
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-300 dark:text-[#334155] px-2 pt-1 pb-2">Filter</p>
                {([
                  { id:"all",         icon:TrendingUp,    label:"All",           count:counts.all },
                  { id:"kol-recent",  icon:Star,          label:"KOL Recent",    count:counts["kol-recent"] },
                  { id:"kol-old",     icon:History,       label:"KOL Historical",count:counts["kol-old"] },
                  { id:"web-article", icon:BookOpen,      label:"Articles",      count:counts["web-article"] },
                  { id:"web-video",   icon:Video,         label:"Videos",        count:counts["web-video"] },
                  { id:"web-social",  icon:MessageCircle, label:"Social",        count:counts["web-social"] },
                ] as { id:SectionFilter; icon:any; label:string; count:number }[]).map(f => (
                  <button key={f.id} onClick={() => setFilter(f.id as SectionFilter)}
                    className={cn(
                      "w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all",
                      filter === f.id
                        ? "bg-pharma-blue text-white"
                        : "text-gray-500 dark:text-[#64748b] hover:bg-gray-50 dark:hover:bg-[#111827] hover:text-gray-800 dark:hover:text-[#94a3b8]"
                    )}>
                    <f.icon size={12} className="shrink-0" />
                    <span className="flex-1 text-left truncate">{f.label}</span>
                    {f.count > 0 && (
                      <span className={cn("text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center",
                        filter === f.id ? "bg-white/20 text-white" : "bg-gray-100 dark:bg-[#1e3a5f]/40 text-gray-400")}>
                        {f.count}
                      </span>
                    )}
                  </button>
                ))}
                <div className="my-2 h-px bg-gray-100 dark:bg-[#1e3a5f]/40" />
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-300 dark:text-[#334155] px-2 pt-1 pb-1">Language</p>
                {[{v:"fr",l:"France only"},{v:"en",l:"English only"},{v:"all",l:"Global (all)"}].map(o => (
                  <button key={o.v} onClick={() => setLangFilter(o.v)}
                    className={cn("w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all",
                      langFilter === o.v ? "bg-pharma-blue text-white" : "text-gray-500 dark:text-[#64748b] hover:bg-gray-50 dark:hover:bg-[#111827] hover:text-gray-800 dark:hover:text-[#94a3b8]")}>
                    {o.l}
                  </button>
                ))}
                <div className="my-2 h-px bg-gray-100 dark:bg-[#1e3a5f]/40" />
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-300 dark:text-[#334155] px-2 pt-1 pb-1">Date range</p>
                <div className="px-2 space-y-1">
                  <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
                    placeholder="From" className="w-full text-[10px] px-2 py-1 rounded border border-gray-200 dark:border-[#1e3a5f] bg-transparent text-gray-600 dark:text-gray-400"/>
                  <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
                    placeholder="To" className="w-full text-[10px] px-2 py-1 rounded border border-gray-200 dark:border-[#1e3a5f] bg-transparent text-gray-600 dark:text-gray-400"/>
                </div>
                <div className="my-2 h-px bg-gray-100 dark:bg-[#1e3a5f]/40" />
              </>
            )}
            {visibleRecents.length > 0 && (
              <>
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-300 dark:text-[#334155] px-2 py-1">Recent</p>
                {visibleRecents.slice(0,10).map(h => (
                  <div key={h.query} className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs text-gray-400 dark:text-[#64748b] hover:text-gray-700 dark:hover:text-[#94a3b8] hover:bg-gray-50 dark:hover:bg-[#111827]/60 transition-all group">
                    <Clock size={9} className="shrink-0 opacity-50"/>
                    <button onClick={() => run(h.query)} className="truncate flex-1 text-left">{h.query}</button>
                    <button onClick={(e) => { e.stopPropagation(); hideRecent(h.query); }}
                      title="Hide from recent (data stays cached)"
                      className="opacity-0 group-hover:opacity-100 hover:text-red-500 transition-opacity shrink-0 p-0.5">
                      <X size={10}/>
                    </button>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>

        {/* ── Main ── */}
        <div className="flex-1 min-w-0 overflow-y-auto">

          {/* Empty state */}
          {!submitted && !isLoading && (
            <div className="flex flex-col items-center justify-center h-full px-6 py-12">
              <div className="relative mb-6">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pharma-blue to-pharma-light flex items-center justify-center shadow-lg shadow-pharma-blue/25">
                  <Globe size={28} className="text-white"/>
                </div>
                <div className="absolute -top-1 -right-1 w-5 h-5 bg-emerald-500 rounded-full flex items-center justify-center shadow-sm">
                  <Zap size={10} className="text-white"/>
                </div>
              </div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-1.5">Pharma Discovery</h2>
              <p className="text-sm text-gray-400 dark:text-[#64748b] text-center max-w-xs mb-6 leading-relaxed">
                Search any drug, congress, KOL, or trial. See what your watch-list KOLs said, plus the latest from across the web.
              </p>
              <div className="flex flex-wrap gap-2 justify-center max-w-sm">
                {TRENDING.map(t => (
                  <button key={t} onClick={() => run(t)}
                    className="px-3 py-1.5 rounded-full bg-white dark:bg-[#111827] border border-gray-200 dark:border-[#1e3a5f] text-sm text-gray-600 dark:text-[#94a3b8] hover:border-pharma-blue hover:text-pharma-blue dark:hover:border-[#2563eb] dark:hover:text-[#93c5fd] hover:shadow-sm transition-all font-medium">
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Loading */}
          {isLoading && (
            <div className="p-5 space-y-5">
              <div className="flex items-center gap-2.5">
                <div className="w-1.5 h-1.5 rounded-full bg-pharma-blue animate-pulse"/>
                <span className="text-sm text-gray-500 dark:text-[#64748b]">
                  Searching <em className="not-italic font-semibold text-gray-800 dark:text-[#e2e8f0]">"{submitted}"</em> across KOLs and the web…
                </span>
              </div>
              <div className="space-y-3">
                {[1,2,3].map(s => (
                  <div key={s} className="h-20 rounded-xl bg-gray-100 dark:bg-[#0d1424] animate-pulse" style={{animationDelay:`${s*80}ms`}}/>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-3 h-40">
                {[0,1,2].map(i => <div key={i} className="rounded-xl bg-gray-200 dark:bg-[#111827] animate-pulse" style={{animationDelay:`${i*60}ms`}}/>)}
              </div>
            </div>
          )}

          {/* Results */}
          {!isLoading && submitted && (
            <div className="p-4 space-y-6">

              {/* ── AI synthesis / takeaway ── */}
              <MarketResearchReport
                question={submitted}
                windowDays={30}
                lang={langFilter}
              />

              <SynthesisPanel
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
                canRegenerate={canGen("discovery_synthesis")}
                onGenerate={() => synthMut.mutate(!!synth)}
                picks={synth?.highlights && synth.highlights.length > 0 ? (
                  <div>
                    <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
                      <Sparkles size={11} className="text-pharma-light" /> Most impactful results
                    </p>
                    <div className="space-y-2">
                      {synth.highlights.map(h => (
                        <div key={h.id} className="space-y-1">
                          <ArticleRow result={h} onClick={() => setActive(h)} />
                          <p className="text-[11px] text-gray-500 dark:text-[#94a3b8] italic px-1 leading-snug">{h.why}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : undefined}
              />

              {/* ── SECTION 1: Latest KOL Mentions ── */}
              {showKolRecent && (kolRecent.length > 0 || kolLoading) && (
                <Section
                  icon={<Star size={15} className="text-amber-500"/>}
                  title="Latest from your KOLs"
                  subtitle={kolRecent.length > 0 ? `${kolRecent.length} recent mention${kolRecent.length!==1?"s":""} (last 6 months)` : "Checking your watch list…"}
                  accent="amber"
                  loading={kolLoading}
                >
                  {kolRecent.map(ins => <KolCard key={ins.id} insight={ins}/>)}
                </Section>
              )}

              {/* ── SECTION 2: Historical KOL Mentions ── */}
              {showKolOld && kolOld.length > 0 && (
                <Section
                  icon={<History size={15} className="text-blue-400"/>}
                  title="Historical KOL mentions"
                  subtitle={`${kolOld.length} older mention${kolOld.length!==1?"s":""}`}
                  accent="blue"
                  collapsible
                >
                  {kolOld.map(ins => <KolCard key={ins.id} insight={ins} muted/>)}
                </Section>
              )}

              {/* ── SECTION: Social Trends (Apify) ── */}
              {showWebSocial && (socialPosts.length > 0 || socialSearching) && (
                <Section
                  icon={<Flame size={15} className="text-orange-500"/>}
                  title="Social Trends"
                  subtitle={socialSearching
                    ? "Pulling latest from Instagram, X, LinkedIn & Facebook…"
                    : `${socialPosts.length} post${socialPosts.length!==1?"s":""} across social platforms`}
                  accent="amber"
                  loading={socialSearching && socialPosts.length === 0}
                >
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {socialPosts.map(p => (
                      <SocialTrendCard key={p.id} post={p} onClick={() => setSocialActive(p)}/>
                    ))}
                  </div>
                </Section>
              )}

              {/* ── SECTION 3: Social Media ── */}
              {showWebSocial && webSocial.length > 0 && (
                <Section
                  icon={<MessageCircle size={15} className="text-sky-500"/>}
                  title="Social Media"
                  subtitle={`${webSocial.length} post${webSocial.length!==1?"s":""} from LinkedIn, X & more`}
                  accent="blue"
                >
                  {/* Hero row — first 3 as cards */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
                    {webSocial.slice(0, 3).map(r => (
                      <SocialCard key={r.id} result={r} onClick={() => setActive(r)}/>
                    ))}
                  </div>
                  {/* Remaining as compact list rows */}
                  {webSocial.length > 3 && (
                    <div className="space-y-2">
                      {webSocial.slice(3).map(r => <SocialListRow key={r.id} result={r} onClick={() => setActive(r)}/>)}
                    </div>
                  )}
                </Section>
              )}

              {/* ── SECTION 4: Videos ── */}
              {showWebVideo && webVideos.length > 0 && (
                <Section
                  icon={<Youtube size={15} className="text-red-500"/>}
                  title="Videos"
                  subtitle={`${webVideos.length} video${webVideos.length!==1?"s":""} found`}
                  accent="red"
                >
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {webVideos.map(r => <WebCard key={r.id} result={r} onClick={() => setActive(r)}/>)}
                  </div>
                </Section>
              )}

              {/* ── SECTION 5: Web Articles ── */}
              {showWebArticle && webArticles.length > 0 && (
                <Section
                  icon={<BookOpen size={15} className="text-pharma-light"/>}
                  title="Articles & Publications"
                  subtitle={`${webArticles.length} article${webArticles.length!==1?"s":""} from across the web`}
                  accent="blue"
                >
                  {/* Hero row */}
                  {webArticles.length >= 3 && (
                    <div className="grid grid-cols-3 gap-3 mb-3" style={{height:"200px"}}>
                      {webArticles.slice(0,3).map(r => (
                        <article key={r.id} onClick={() => setActive(r)}
                          className="relative rounded-xl overflow-hidden cursor-pointer group shadow-sm hover:shadow-md transition-shadow">
                          {r.thumbnail_url
                            ? <img src={r.thumbnail_url} alt="" className="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"/>
                            : <div className="absolute inset-0" style={{background:domainBg(r.source_name||getDomain(r.url))}}>
                                <svg className="absolute inset-0 w-full h-full opacity-[0.04]" xmlns="http://www.w3.org/2000/svg">
                                  <pattern id={`dp-${r.id}`} x="0" y="0" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="9" cy="9" r="1.2" fill="white"/></pattern>
                                  <rect width="100%" height="100%" fill={`url(#dp-${r.id})`}/>
                                </svg>
                              </div>
                          }
                          <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent"/>
                          <div className="absolute top-2 left-2">
                            <SourcePill domain={r.source_name||getDomain(r.url)}/>
                          </div>
                          <div className="absolute bottom-0 left-0 right-0 p-3">
                            <p className="text-white font-semibold text-xs leading-snug line-clamp-3 mb-1.5">
                              {r.snippet||r.title||getDomain(r.url)}
                            </p>
                            <DateTag label={r.published_date||fmt(r.scraped_at)} type={r.published_date?"pub":"fetch"}/>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                  {/* List rows */}
                  <div className="space-y-2">
                    {webArticles.slice(webArticles.length>=3?3:0).map(r => (
                      <ArticleRow key={r.id} result={r} onClick={() => setActive(r)} onDescribe={() => setDescribe(r)}/>
                    ))}
                  </div>
                </Section>
              )}

              {/* Nothing at all */}
              {!kolLoading && kolTotal === 0 && webAll.length === 0 && (
                <div className="flex flex-col items-center justify-center py-16 text-gray-400">
                  <AlertCircle size={24} className="mb-3 opacity-30"/>
                  <p className="text-sm font-medium">No results for "{submitted}"</p>
                  <p className="text-xs mt-1 opacity-60">Try broader terms</p>
                </div>
              )}

            </div>
          )}
        </div>
      </div>

      {/* ══ CONTENT MODAL ══ */}
      {active && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={() => setActive(null)}>
          <div className="bg-white dark:bg-[#111827] rounded-2xl shadow-2xl w-full max-w-xl max-h-[85vh] flex flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}>
            <DetailModal result={active} onClose={() => setActive(null)}/>
          </div>
        </div>
      )}

      {/* ══ DESCRIBE RIGHT PANEL ══ */}
      {describeResult && (
        <DescribePanel result={describeResult} onClose={() => setDescribe(null)}/>
      )}

      {deepOpen && submitted && (
        <DeepSearchModal query={submitted} onClose={() => setDeepOpen(false)}/>
      )}

      {socialActive && (
        <SocialDescribeModal post={socialActive} onClose={() => setSocialActive(null)}/>
      )}
    </div>
  );
}

/* ─── social trend card (Apify) ──────────────────────────── */

const SOCIAL_PLATFORM_CLS: Record<string, string> = {
  instagram: "bg-pink-100 text-pink-700 dark:bg-pink-900/20 dark:text-pink-300",
  twitter: "bg-sky-100 text-sky-700 dark:bg-sky-900/20 dark:text-sky-300",
  linkedin: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200",
  facebook: "bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300",
};

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n/1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n/1_000).toFixed(1)}k`;
  return `${n}`;
}

function SocialTrendCard({ post, onClick }: { post: SocialPost; onClick: () => void }) {
  return (
    <div onClick={onClick}
      className="glass-panel rounded-xl overflow-hidden cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all flex flex-col">
      {post.thumbnail_url && (
        <div className="h-28 bg-slate-100 dark:bg-[#0d1424] overflow-hidden">
          <img src={post.thumbnail_url} alt="" loading="lazy" className="w-full h-full object-cover"
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}/>
        </div>
      )}
      <div className="p-3 flex-1 flex flex-col">
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium", SOCIAL_PLATFORM_CLS[post.platform] ?? "bg-gray-100 text-gray-600")}>
            {post.platform}
          </span>
          {post.author && <span className="text-[11px] font-semibold text-pharma-light truncate">{post.author}</span>}
        </div>
        <p className="text-xs text-gray-600 dark:text-[#94a3b8] line-clamp-3 flex-1">{post.text || "—"}</p>
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-gray-100 dark:border-[#1e3a5f]/40 text-[11px] text-gray-500 dark:text-[#64748b]">
          {post.likes > 0 && <span className="flex items-center gap-1"><Heart size={11}/>{compact(post.likes)}</span>}
          {post.comments > 0 && <span className="flex items-center gap-1"><MessageCircle size={11}/>{compact(post.comments)}</span>}
        </div>
      </div>
    </div>
  );
}

/* ─── deep search modal ──────────────────────────────────── */

const DEEP_FILTER_OPTIONS = [
  { id: "all",      label: "All" },
  { id: "research", label: "Research" },
  { id: "video",    label: "Video" },
  { id: "linkedin", label: "LinkedIn" },
  { id: "twitter",  label: "X/Twitter" },
  { id: "social",   label: "Social" },
  { id: "article",  label: "Article" },
  { id: "pdf",      label: "PDF" },
];

function DeepSearchModal({ query, onClose }: { query: string; onClose: () => void }) {
  const [typeFilter, setTypeFilter] = useState("all");
  const deepMut = useMutation({ mutationFn: (q: string) => api.discovery.deepSearch(q) });

  useEffect(() => { deepMut.mutate(query); }, [query]);

  const results = deepMut.data?.results ?? [];
  const filtered = typeFilter === "all" ? results : results.filter(r => r.media_type === typeFilter);
  const counts: Record<string, number> = { all: results.length };
  for (const r of results) counts[r.media_type] = (counts[r.media_type] || 0) + 1;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#f8fafc] dark:bg-[#070c19]">
      {/* Header */}
      <div className="flex-none glass-panel border-b border-slate-200/50 dark:border-white/10 px-5 py-3 flex items-center gap-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-pharma-blue to-pharma-light flex items-center justify-center shadow">
            <ScanSearch size={16} className="text-white"/>
          </div>
          <div>
            <p className="text-sm font-bold text-gray-900 dark:text-white">Deep Search</p>
            <p className="text-xs text-gray-400 dark:text-[#64748b]">
              Everything on the internet about <span className="font-semibold text-pharma-light">"{query}"</span>
            </p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {deepMut.isPending && (
            <span className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-[#64748b]">
              <FlaskConical size={13} className="animate-pulse text-pharma-blue"/>
              Scanning the web…
            </span>
          )}
          {!deepMut.isPending && (
            <span className="text-xs font-semibold text-gray-500 dark:text-[#64748b]">
              {results.length} results found
            </span>
          )}
          <button onClick={onClose}
            className="w-8 h-8 rounded-full flex items-center justify-center text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/40 hover:text-gray-600 transition-all">
            <X size={16}/>
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex-none glass-panel border-b border-slate-200/50 dark:border-white/10 px-5 py-2 flex items-center gap-1.5 overflow-x-auto">
        {DEEP_FILTER_OPTIONS.map(f => (
          counts[f.id] !== undefined || f.id === "all" ? (
            <button key={f.id} onClick={() => setTypeFilter(f.id)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all",
                typeFilter === f.id
                  ? "bg-pharma-blue text-white"
                  : "text-gray-500 dark:text-[#64748b] hover:bg-gray-50 dark:hover:bg-[#111827] hover:text-gray-800 dark:hover:text-[#94a3b8]"
              )}>
              {f.label}
              <span className={cn("text-[10px] font-bold px-1 py-0.5 rounded-full",
                typeFilter === f.id ? "bg-white/20 text-white" : "bg-gray-100 dark:bg-[#1e3a5f]/40 text-gray-400")}>
                {counts[f.id] || 0}
              </span>
            </button>
          ) : null
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {/* Loading skeleton */}
        {deepMut.isPending && (
          <div className="space-y-3 max-w-3xl mx-auto">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="h-16 rounded-xl bg-gray-100 dark:bg-[#0d1424] animate-pulse"
                style={{ animationDelay: `${i * 60}ms`, opacity: 1 - i * 0.06 }}/>
            ))}
            <p className="text-center text-xs text-gray-400 dark:text-[#475569] pt-4">
              Searching across LinkedIn, Twitter, YouTube, PubMed, news sites, and more…
            </p>
          </div>
        )}

        {/* Results — newest to oldest timeline */}
        {!deepMut.isPending && filtered.length > 0 && (
          <div className="max-w-3xl mx-auto space-y-2">
            {filtered.map((r, i) => (
              <DeepResultRow key={r.id} result={r} index={i}/>
            ))}
          </div>
        )}

        {/* Empty */}
        {!deepMut.isPending && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 text-gray-400">
            <AlertCircle size={28} className="mb-3 opacity-30"/>
            <p className="text-sm">No results found for this filter</p>
          </div>
        )}

        {/* Error */}
        {deepMut.isError && (
          <div className="flex flex-col items-center justify-center h-64 text-red-400">
            <AlertCircle size={28} className="mb-3"/>
            <p className="text-sm">Deep search failed. Please try again.</p>
            <button onClick={() => deepMut.mutate(query)}
              className="mt-3 text-xs text-pharma-light underline">Retry</button>
          </div>
        )}
      </div>
    </div>
  );
}

function DeepResultRow({ result, index }: { result: DiscoveryResult; index: number }) {
  const domain = result.source_name || getDomain(result.url);
  const label = mediaLabel(result.media_type);
  const labelClass = mediaLabelClass(result.media_type);
  const date = result.published_date || fmt(result.scraped_at);
  const dateType = result.published_date ? "pub" : "fetch";
  const isVideo = result.media_type === "video";
  const bg = domainBg(domain);

  return (
    <a href={result.url} target="_blank" rel="noreferrer"
      className="flex items-start gap-3 p-3.5 glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 group hover:border-pharma-blue/30 hover:shadow-sm transition-all">
      {/* Rank */}
      <span className="text-xs font-bold text-gray-300 dark:text-[#334155] w-5 shrink-0 pt-0.5 text-right">
        {index + 1}
      </span>

      {/* Thumbnail or color swatch */}
      <div className="w-14 h-10 rounded-lg overflow-hidden shrink-0 flex items-center justify-center"
        style={{ background: result.thumbnail_url ? undefined : bg }}>
        {result.thumbnail_url
          ? <img src={result.thumbnail_url} alt="" className="w-full h-full object-cover"/>
          : isVideo
            ? <Play size={14} className="text-white/60" fill="currentColor"/>
            : <span className="text-sm font-black text-white/20 select-none">{domain.charAt(0).toUpperCase()}</span>
        }
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <img src={`https://www.google.com/s2/favicons?sz=12&domain=${domain}`} alt="" className="w-3 h-3 rounded shrink-0"
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}/>
          <span className="text-[10px] font-semibold text-pharma-light">{domain}</span>
          {label && (
            <span className={cn("text-[9px] font-bold px-1.5 py-0.5 rounded", labelClass)}>{label}</span>
          )}
          <span className="ml-auto flex items-center gap-1 text-[10px] shrink-0"
            style={{ color: dateType === "pub" ? "#94a3b8" : "#64748b" }}>
            <Clock size={9}/>{dateType === "fetch" && <span className="opacity-60">fetched</span>}{date}
          </span>
        </div>
        <p className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0] line-clamp-2 leading-snug group-hover:text-pharma-blue dark:group-hover:text-[#93c5fd] transition-colors">
          {result.snippet || result.title || domain}
        </p>
        <p className="text-[10px] text-gray-300 dark:text-[#334155] truncate mt-0.5 flex items-center gap-1">
          <Link2 size={9}/>{result.url}
        </p>
      </div>

      <ExternalLink size={13} className="text-gray-300 dark:text-[#334155] group-hover:text-pharma-blue shrink-0 mt-1 transition-colors"/>
    </a>
  );
}

/* ─── section wrapper ────────────────────────────────────── */

function Section({ icon, title, subtitle, accent, collapsible, loading, children }: {
  icon: React.ReactNode; title: string; subtitle: string;
  accent: "amber"|"blue"|"red"; collapsible?: boolean; loading?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const accentLine = { amber:"bg-amber-400", blue:"bg-pharma-blue", red:"bg-red-500" }[accent];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <div className={cn("w-0.5 h-5 rounded-full", accentLine)}/>
        <div className="flex items-center gap-2 flex-1">
          {icon}
          <h2 className="text-sm font-bold text-gray-900 dark:text-[#e2e8f0]">{title}</h2>
          <span className="text-xs text-gray-400 dark:text-[#64748b]">{subtitle}</span>
        </div>
        {collapsible && (
          <button onClick={() => setOpen(o => !o)} className="text-gray-400 hover:text-gray-600 transition-colors">
            {open ? <ChevronUp size={15}/> : <ChevronDown size={15}/>}
          </button>
        )}
      </div>
      {open && (
        <div>
          {loading
            ? <div className="space-y-2">{[0,1,2].map(i => <div key={i} className="h-16 rounded-xl bg-gray-100 dark:bg-[#0d1424] animate-pulse" style={{animationDelay:`${i*60}ms`}}/>)}</div>
            : children
          }
        </div>
      )}
    </div>
  );
}

/* ─── KOL insight card ───────────────────────────────────── */

function KolCard({ insight, muted }: { insight: KolInsight; muted?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const sent = insight.sentiment as string;

  return (
    <div className={cn(
      "glass-panel rounded-xl border overflow-hidden transition-all",
      muted ? "border-slate-200/50 dark:border-white/5" : "border-slate-200/50 dark:border-white/10"
    )}>
      <div className="flex items-start gap-3 p-3.5">
        {/* KOL avatar */}
        <div className="w-8 h-8 rounded-full bg-pharma-blue/10 dark:bg-[#2563eb]/15 flex items-center justify-center text-xs font-bold text-pharma-blue dark:text-[#93c5fd] shrink-0">
          {insight.kol.charAt(0)}
        </div>
        <div className="flex-1 min-w-0">
          {/* KOL name + date + sentiment */}
          <div className="flex items-center flex-wrap gap-2 mb-1.5">
            <span className="text-sm font-semibold text-gray-900 dark:text-[#e2e8f0]">{insight.kol}</span>
            <span className={cn("flex items-center gap-0.5 text-[10px] border px-1.5 py-0.5 rounded-full font-semibold", SENT_STYLE[sent]||SENT_STYLE.neutral)}>
              <span className={cn("w-1.5 h-1.5 rounded-full", SENT_DOT[sent]||SENT_DOT.neutral)}/>
              {sent}
            </span>
            {insight.category && (
              <span className="text-[10px] text-gray-400 dark:text-[#64748b] bg-gray-50 dark:bg-[#1e3a5f]/20 px-1.5 py-0.5 rounded-full">
                {insight.category.replace(/_/g," ")}
              </span>
            )}
            <span className="ml-auto text-xs text-gray-400 dark:text-[#64748b] flex items-center gap-1 shrink-0">
              <Clock size={10}/>{insight.published_date || "—"}
            </span>
          </div>
          {/* Topic */}
          <p className="text-xs font-medium text-gray-600 dark:text-[#94a3b8] mb-1">{insight.topic}</p>
          {/* Quote */}
          <p className={cn("text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed", expanded ? "" : "line-clamp-2")}>
            {insight.what_they_said}
          </p>
          {insight.what_they_said && insight.what_they_said.length > 120 && (
            <button onClick={() => setExpanded(e => !e)} className="text-xs text-pharma-light hover:text-pharma-blue mt-1 font-medium transition-colors">
              {expanded ? "Show less" : "Read more"}
            </button>
          )}
        </div>
        {/* Source link */}
        {insight.source_url && (
          <a href={insight.source_url} target="_blank" rel="noreferrer"
            onClick={e => e.stopPropagation()}
            className="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-gray-300 dark:text-[#334155] hover:text-pharma-blue hover:bg-gray-50 dark:hover:bg-[#1e3a5f]/30 transition-all">
            <ExternalLink size={13}/>
          </a>
        )}
      </div>
    </div>
  );
}

/* ─── web result cards ───────────────────────────────────── */

function WebCard({ result, onClick }: { result: DiscoveryResult; onClick: () => void }) {
  const domain = result.source_name || getDomain(result.url);
  const isVideo = result.media_type === "video";
  const label = mediaLabel(result.media_type);
  return (
    <article onClick={onClick} className="relative rounded-xl overflow-hidden cursor-pointer group h-44 shadow-sm hover:shadow-md transition-shadow">
      {result.thumbnail_url
        ? <img src={result.thumbnail_url} alt="" className="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"/>
        : <div className="absolute inset-0" style={{background:domainBg(domain)}}/>
      }
      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent"/>
      {isVideo && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-10 h-10 rounded-full bg-white/25 backdrop-blur-sm border border-white/30 flex items-center justify-center">
            <Play size={16} className="text-white ml-0.5" fill="white"/>
          </div>
        </div>
      )}
      <div className="absolute top-2 left-2 flex items-center gap-1.5">
        <SourcePill domain={domain}/>
        {label && <span className="text-[9px] font-bold bg-white/20 backdrop-blur-sm text-white px-1.5 py-0.5 rounded">{label}</span>}
      </div>
      <div className="absolute bottom-0 left-0 right-0 p-2.5">
        <p className="text-white font-semibold text-xs leading-snug line-clamp-2 mb-1">
          {result.snippet||result.title||domain}
        </p>
        <DateTag label={result.published_date||fmt(result.scraped_at)} type={result.published_date?"pub":"fetch"}/>
      </div>
    </article>
  );
}

function SocialCard({ result, onClick }: { result: DiscoveryResult; onClick: () => void }) {
  const domain = result.source_name || getDomain(result.url);
  const isLinkedIn = result.media_type === "linkedin";
  const isTwitter  = result.media_type === "twitter";
  const date = result.published_date || fmt(result.scraped_at);
  const dateType = result.published_date ? "pub" : "fetch";

  const platformBg   = isLinkedIn ? "#0a66c2" : isTwitter ? "#14171a" : "#6366f1";
  const platformName = isLinkedIn ? "LinkedIn" : isTwitter ? "X / Twitter" : "Social";
  const PlatformIcon = isLinkedIn ? Linkedin : MessageCircle;

  return (
    <article onClick={onClick}
      className="glass-panel rounded-2xl border border-slate-200/50 dark:border-white/10 overflow-hidden cursor-pointer group hover:border-pharma-blue/30 hover:shadow-md transition-all flex flex-col">

      {/* Branded platform header */}
      <div className="relative h-28 shrink-0 flex items-center justify-center overflow-hidden"
        style={{ background: `linear-gradient(135deg, ${platformBg} 0%, ${platformBg}bb 100%)` }}>
        <svg className="absolute inset-0 w-full h-full opacity-[0.05]" xmlns="http://www.w3.org/2000/svg">
          <pattern id={`sp-${result.id}`} x="0" y="0" width="18" height="18" patternUnits="userSpaceOnUse">
            <circle cx="9" cy="9" r="1.2" fill="white"/>
          </pattern>
          <rect width="100%" height="100%" fill={`url(#sp-${result.id})`}/>
        </svg>
        <PlatformIcon size={36} className="text-white/20"/>
        <div className="absolute top-2.5 left-2.5">
          <div className="flex items-center gap-1 text-[10px] font-bold text-white px-2 py-0.5 rounded-full"
            style={{ background: "rgba(0,0,0,0.25)" }}>
            <PlatformIcon size={9}/>{platformName}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="p-3.5 flex flex-col gap-2 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-semibold text-gray-400 dark:text-[#64748b] truncate">{domain}</span>
          <DateTag label={date} type={dateType} small/>
        </div>
        <p className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0] leading-snug line-clamp-3 group-hover:text-pharma-blue dark:group-hover:text-[#93c5fd] transition-colors flex-1">
          {result.snippet || result.title || `Post on ${platformName}`}
        </p>
        <div className="flex items-center justify-between pt-2 border-t border-gray-50 dark:border-[#1e3a5f]/40">
          <span className="text-[9px] text-gray-300 dark:text-[#334155] truncate flex-1 mr-2">
            {result.url.length > 42 ? result.url.slice(0, 42) + "…" : result.url}
          </span>
          <ExternalLink size={12} className="text-gray-300 dark:text-[#334155] group-hover:text-pharma-blue transition-colors shrink-0"/>
        </div>
      </div>
    </article>
  );
}

function SocialListRow({ result, onClick }: { result: DiscoveryResult; onClick: () => void }) {
  const domain = result.source_name || getDomain(result.url);
  const isLinkedIn = result.media_type === "linkedin";
  const isTwitter  = result.media_type === "twitter";
  const platformBg   = isLinkedIn ? "#0a66c2" : isTwitter ? "#14171a" : "#6366f1";
  const platformName = isLinkedIn ? "LinkedIn" : isTwitter ? "X / Twitter" : "Social";
  const PlatformIcon = isLinkedIn ? Linkedin : MessageCircle;
  const date = result.published_date || fmt(result.scraped_at);
  const dateType = result.published_date ? "pub" : "fetch";

  return (
    <article onClick={onClick}
      className="flex items-start gap-3 px-3 py-2.5 glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 cursor-pointer group hover:border-pharma-blue/30 hover:shadow-sm transition-all">
      {/* Platform icon */}
      <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
        style={{ background: platformBg }}>
        <PlatformIcon size={14} className="text-white"/>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[10px] font-bold text-white/80 px-1.5 py-0.5 rounded"
            style={{ background: platformBg }}>{platformName}</span>
          <span className="text-[10px] text-gray-400 dark:text-[#64748b] truncate">{domain}</span>
          <span className="ml-auto text-[10px] text-gray-400 flex items-center gap-1 shrink-0">
            <Clock size={9}/>{dateType === "fetch" && <span className="opacity-60">fetched</span>}{date}
          </span>
        </div>
        <p className="text-sm font-medium text-gray-700 dark:text-[#e2e8f0] line-clamp-2 group-hover:text-pharma-blue dark:group-hover:text-[#93c5fd] transition-colors leading-snug">
          {result.snippet || result.title || `Post on ${platformName}`}
        </p>
      </div>
      <ExternalLink size={12} className="text-gray-300 dark:text-[#334155] group-hover:text-pharma-blue shrink-0 mt-1 transition-colors"/>
    </article>
  );
}

function ArticleRow({ result, onClick, onDescribe }: { result: DiscoveryResult; onClick: () => void; onDescribe?: () => void }) {
  const domain = result.source_name || getDomain(result.url);
  const bg = domainBg(domain);
  return (
    <article onClick={onClick}
      className="flex items-center gap-3 px-3 py-2.5 glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 cursor-pointer group hover:border-pharma-blue/30 hover:shadow-sm transition-all">
      <div className="w-12 h-9 rounded-lg overflow-hidden shrink-0 flex items-center justify-center"
        style={{background:result.thumbnail_url?undefined:bg}}>
        {result.thumbnail_url
          ? <img src={result.thumbnail_url} alt="" className="w-full h-full object-cover"/>
          : <span className="text-base font-black text-white/20 select-none">{domain.charAt(0).toUpperCase()}</span>
        }
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-[10px] font-semibold text-pharma-light truncate">{domain}</span>
          {mediaLabel(result.media_type) && (
            <span className={cn("text-[9px] font-bold px-1 py-0.5 rounded shrink-0", mediaLabelClass(result.media_type))}>
              {mediaLabel(result.media_type)}
            </span>
          )}
        </div>
        <p className="text-xs font-medium text-gray-700 dark:text-[#e2e8f0] line-clamp-1 group-hover:text-pharma-blue dark:group-hover:text-[#93c5fd] transition-colors">
          {result.snippet||result.title||domain}
        </p>
      </div>
      <DateTag label={result.published_date||fmt(result.scraped_at)} type={result.published_date?"pub":"fetch"} small/>
      {onDescribe && (
        <button onClick={e => { e.stopPropagation(); onDescribe(); }}
          title="AI Describe"
          className="p-1 rounded hover:bg-pharma-blue/10 text-gray-300 hover:text-pharma-blue dark:hover:text-blue-400 transition-colors shrink-0">
          <Sparkles size={12}/>
        </button>
      )}
      <ExternalLink size={12} className="text-gray-300 dark:text-[#334155] group-hover:text-pharma-blue shrink-0 transition-colors"/>
    </article>
  );
}

/* ─── describe right panel ───────────────────────────────── */

function DescribePanel({ result, onClose }: { result: DiscoveryResult; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["disc-describe", result.id],
    queryFn: () => api.discovery.describe(result.id),
    staleTime: Infinity,
  });
  const domain = result.source_name || getDomain(result.url);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose}/>
      <div className="relative w-full max-w-md bg-white dark:bg-[#0d1424] border-l border-slate-200 dark:border-white/10 flex flex-col shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-slate-200/50 dark:border-white/10 flex-none">
          <Sparkles size={16} className="text-pharma-blue shrink-0"/>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold text-gray-400 truncate">{domain}</p>
            <p className="text-sm font-bold text-gray-900 dark:text-white line-clamp-1">{result.title || result.snippet || domain}</p>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/40 rounded-lg transition-colors text-gray-400">
            <X size={16}/>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {isLoading ? (
            <div className="space-y-3">
              {[80,60,90].map((w,i) => <div key={i} className="h-4 rounded bg-gray-100 dark:bg-[#1e3a5f]/40 animate-pulse" style={{width:`${w}%`}}/>)}
            </div>
          ) : data ? (
            <>
              <div>
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">What this is</p>
                <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.description}</p>
              </div>
              {data.so_what && (
                <div className="bg-pharma-blue/5 dark:bg-pharma-blue/10 rounded-xl p-4 border border-pharma-blue/20">
                  <p className="text-[10px] font-black uppercase tracking-widest text-pharma-blue mb-2">So what for Roche?</p>
                  <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.so_what}</p>
                </div>
              )}
              <a href={result.url} target="_blank" rel="noreferrer"
                className="flex items-center gap-2 text-xs text-pharma-blue hover:underline">
                <ExternalLink size={11}/> View source
              </a>
            </>
          ) : (
            <p className="text-sm text-gray-400">Failed to load description.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── detail modal ───────────────────────────────────────── */

function DetailModal({ result, onClose }: { result: DiscoveryResult; onClose: () => void }) {
  const [fetched, setFetched] = useState<DiscoveryContent | null>(
    result.content ? { content: result.content, media_type: result.media_type, blocked: false } : null
  );
  const [loading, setLoading] = useState(false);
  const isVideo = result.media_type === "video";
  const domain  = result.source_name || getDomain(result.url);

  useEffect(() => {
    if (!isVideo && !fetched) {
      setLoading(true);
      api.discovery.fetchContent(result.id, result.url)
        .then(r => { setFetched(r); setLoading(false); })
        .catch(() => setLoading(false));
    }
  }, [result.id]);

  return (
    <>
      <div className="flex items-start gap-3 px-5 py-4 border-b border-gray-100 dark:border-[#1e3a5f] shrink-0">
        <div className="flex-1 min-w-0">
          <div className="flex items-center flex-wrap gap-1.5 mb-1.5">
            <img src={`https://www.google.com/s2/favicons?sz=14&domain=${domain}`} alt="" className="w-3.5 h-3.5 rounded opacity-80"
              onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}/>
            <span className="text-xs font-semibold text-pharma-light">{domain}</span>
            <span className="text-gray-200 dark:text-gray-700">·</span>
            <DateTag label={result.published_date||fmt(result.scraped_at)} type={result.published_date?"pub":"fetch"} small/>
            {isVideo && <span className="flex items-center gap-1 text-[9px] bg-red-100 text-red-600 dark:bg-red-900/20 dark:text-red-400 px-1.5 py-0.5 rounded font-bold"><Youtube size={9}/>YouTube</span>}
          </div>
          <p className="text-sm font-semibold text-gray-900 dark:text-[#e2e8f0] leading-snug line-clamp-2">
            {result.snippet||result.title||domain}
          </p>
        </div>
        <button onClick={onClose} className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/40 transition-all">
          <X size={15}/>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {isVideo && (
          <div className="aspect-video rounded-xl overflow-hidden bg-black shadow-md">
            <iframe src={`https://www.youtube.com/embed/${ytId(result.url)}`}
              className="w-full h-full" allowFullScreen title="YouTube"/>
          </div>
        )}
        {!isVideo && result.thumbnail_url && (
          <div className="h-36 rounded-xl overflow-hidden">
            <img src={result.thumbnail_url} alt="" className="w-full h-full object-cover"/>
          </div>
        )}
        {!isVideo && loading && (
          <div className="space-y-2 py-2">
            {[95,80,90,70,85].map((w,i) => <div key={i} className="h-3.5 rounded-full bg-gray-100 dark:bg-[#1e3a5f]/30 animate-pulse" style={{width:`${w}%`,animationDelay:`${i*50}ms`}}/>)}
          </div>
        )}
        {!isVideo && !loading && fetched?.blocked && (
          <div className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/15 rounded-xl px-4 py-3 border border-amber-200 dark:border-amber-800/30">
            <Lock size={14}/>Protected page — open directly to read.
          </div>
        )}
        {!isVideo && !loading && fetched?.error==="timeout" && (
          <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/15 rounded-xl px-4 py-3 border border-red-200 dark:border-red-800/30">
            <AlertCircle size={14}/>Timed out.
            <a href={result.url} target="_blank" rel="noreferrer" className="underline font-medium ml-auto">Open →</a>
          </div>
        )}
        {!isVideo && !loading && fetched?.content && <ContentRenderer text={fetched.content}/>}
        {!isVideo && !loading && !fetched?.content && !fetched?.blocked && !fetched?.error && (
          <div className="text-center py-6 text-gray-400">
            <FileText size={22} className="mx-auto mb-2 opacity-30"/>
            <p className="text-sm">No content extracted.</p>
            <a href={result.url} target="_blank" rel="noreferrer" className="text-pharma-light text-xs underline">Open original →</a>
          </div>
        )}
      </div>

      <div className="px-5 py-3.5 border-t border-gray-100 dark:border-[#1e3a5f] shrink-0">
        <a href={result.url} target="_blank" rel="noreferrer"
          className="flex items-center justify-center gap-2 w-full py-2.5 bg-pharma-blue hover:bg-pharma-light text-white rounded-xl text-sm font-semibold transition-colors shadow-sm">
          <ExternalLink size={13}/>{isVideo?"Watch on YouTube":"Read Full Article"}
        </a>
      </div>
    </>
  );
}

/* ─── content renderer ───────────────────────────────────── */

function ContentRenderer({ text }: { text: string }) {
  const lines = text.split("\n").filter(l => l.trim());
  const pubs: any[] = []; const prose: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i].trim();
    const y1 = l.match(/^(20\d{2}|19\d{2})$/);
    if (y1 && lines[i+1]) {
      const title = lines[i+1].trim(), jl = lines[i+2]?.trim()||"";
      const jm = jl.match(/^(.+?)\s*[-–]\s*(.+)$/);
      pubs.push({ year:y1[1], title, journal:jm?.[1]?.trim()||jl, date:jm?.[2]?.trim()||"" });
      i += jm ? 3 : 2; continue;
    }
    const y2 = l.match(/^(20\d{2}|19\d{2})\s+(.+)$/);
    if (y2) {
      const jl = lines[i+1]?.trim()||"", jm = jl.match(/^(.+?)\s*[-–]\s*(.+)$/);
      pubs.push({ year:y2[1], title:y2[2], journal:jm?.[1]?.trim()||jl, date:jm?.[2]?.trim()||"" });
      i += (jm&&jl)?2:1; continue;
    }
    if (!l.includes(">") && !l.includes("http")) prose.push(l);
    i++;
  }
  return (
    <div className="space-y-4 max-h-64 overflow-y-auto">
      {prose.length>0 && <div className="space-y-2">{prose.map((p,i)=><p key={i} className="text-sm text-gray-700 dark:text-[#94a3b8] leading-relaxed">{p}</p>)}</div>}
      {pubs.length>0 && (
        <div className="space-y-2">
          <p className="text-[10px] font-black uppercase tracking-widest text-gray-300 dark:text-[#334155]">Publications</p>
          {pubs.map((p,i)=>(
            <div key={i} className="flex gap-3 p-3 rounded-xl bg-gray-50 dark:bg-[#0a0f1e] border border-gray-100 dark:border-[#1e3a5f]/50">
              <span className="w-10 h-10 rounded-lg bg-pharma-blue/10 flex items-center justify-center text-[11px] font-bold text-pharma-blue dark:text-[#93c5fd] shrink-0">{p.year}</span>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-gray-900 dark:text-[#e2e8f0] leading-snug line-clamp-2 mb-1">{p.title}</p>
                {(p.journal||p.date) && (
                  <div className="flex items-center gap-1.5 text-xs">
                    {p.journal && <span className="text-pharma-light font-medium">{p.journal}</span>}
                    {p.date && <><span className="text-gray-300">·</span><span className="text-gray-400">{p.date}</span></>}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── micro components ───────────────────────────────────── */

function Chip({ icon, label, cls }: { icon: React.ReactNode; label: string; cls: string }) {
  return <span className={cn("flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full", cls)}>{icon}{label}</span>;
}

function SourcePill({ domain }: { domain: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-white/85 bg-black/35 backdrop-blur-sm px-2 py-0.5 rounded-full truncate max-w-[130px]">
      <img src={`https://www.google.com/s2/favicons?sz=10&domain=${domain}`} alt="" className="w-2.5 h-2.5 rounded shrink-0"
        onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}/>
      {domain}
    </span>
  );
}

function DateTag({ label, type, small }: { label: string; type:"pub"|"fetch"; small?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1", small?"text-[10px]":"text-xs",
      type==="pub"?"text-white/60":"text-gray-400 dark:text-[#475569]")}>
      <Clock size={small?8:10}/>
      {type==="fetch" && <span className="opacity-60 mr-0.5">fetched</span>}
      {label}
    </span>
  );
}

/* ─── utils ──────────────────────────────────────────────── */

function getDomain(url: string): string {
  try { return new URL(url).hostname.replace("www.",""); } catch { return ""; }
}
function ytId(url: string): string {
  const m = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
  return m?m[1]:"";
}
function fmt(iso: string): string {
  try { return new Date(iso).toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"}); }
  catch { return ""; }
}
