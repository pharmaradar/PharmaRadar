import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useMemo, useEffect } from "react";
import { Play, Square, RefreshCw, TrendingUp, Users, FileText, Clock, BarChart2, ExternalLink, Filter, X, Sparkles, Loader2, ChevronRight, ChevronDown } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import { api, type Insight, type DailyBriefPoint } from "@/lib/api";
import { formatDateTime, SENTIMENT_COLORS, cn } from "@/lib/utils";
import { useAppStore } from "@/store";
import { useAuthStore } from "@/store/auth";
import SocialTrendsSummary from "./SocialTrendsSummary";
import { SynthesisPanel } from "@/components/SynthesisPanel";
import { useGenQuota } from "@/hooks/useGenQuota";

const PIE_COLORS = ["#0066cc", "#0ea5e9", "#14b8a6", "#f59e0b", "#6366f1", "#8b5cf6", "#ec4899", "#f43f5e"];
const PHARMA_BLUE = "#0066cc";

const PERIOD_OPTIONS = [
  { label: "7 days",  value: 7  },
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
];

function StatCard({ label, value, icon: Icon, sub }: {
  label: string; value: string | number; icon: React.ElementType; sub?: string;
}) {
  return (
    <div className="glass rounded-xl p-5 relative overflow-hidden group">
      <div className="flex items-center justify-between mb-3 relative">
        <span className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</span>
        <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
          <Icon size={18} className="text-blue-600 dark:text-blue-400" />
        </div>
      </div>
      <div className="text-3xl font-bold text-slate-800 dark:text-slate-100 relative">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-2 relative">{sub}</div>}
    </div>
  );
}

export default function Dashboard() {
  const qc = useQueryClient();
  const { setActiveRunId } = useAppStore();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const authUser = useAuthStore((s) => s.user);
  const greetName = authUser?.name?.trim() || authUser?.email?.split("@")[0] || "there";
  const [period, setPeriod] = useState(7);

  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 10_000 });
  const { data: currentRun } = useQuery({
    queryKey: ["current-run"],
    queryFn: api.runs.current,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : 10_000),
  });
  const [filterTarget, setFilterTarget] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [filterSentiment, setFilterSentiment] = useState("");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;
  const [drawerInsight, setDrawerInsight] = useState<Insight | null>(null);

  // Chart click → slide-over panel
  const [chartPanel, setChartPanel] = useState<{ label: string; type: "category"|"sentiment"|"kol"|"topic"; value: string } | null>(null);

  const { data: insights } = useQuery({ queryKey: ["latest-insights"], queryFn: () => api.reports.latest(100) });

  const chartPanelInsights = useMemo(() => {
    if (!chartPanel || !insights) return [];
    const { type, value } = chartPanel;
    const norm = (s: string) => s.toLowerCase().replace(/_/g, " ");
    return insights.filter(i => {
      if (type === "sentiment") return norm(i.sentiment || "") === norm(value);
      if (type === "category")  return norm(i.category  || "") === norm(value);
      if (type === "kol")       return i.target_name === value;
      if (type === "topic")     return norm(i.topic || "").includes(norm(value));
      return false;
    });
  }, [chartPanel, insights]);

  const targets = useMemo(() => [...new Set((insights ?? []).map(i => i.target_name))].sort(), [insights]);
  const categories = useMemo(() => [...new Set((insights ?? []).map(i => i.category).filter(Boolean))].sort(), [insights]);

  const oneYearAgo = useMemo(() => {
    const d = new Date(); d.setFullYear(d.getFullYear() - 1); return d.toISOString().slice(0, 10);
  }, []);

  const filtered = useMemo(() =>
    (insights ?? []).filter(i =>
      (!filterTarget || i.target_name === filterTarget) &&
      (!filterCategory || i.category === filterCategory) &&
      (!filterSentiment || i.sentiment === filterSentiment) &&
      (!i.published_date || i.published_date >= oneYearAgo)
    ),
    [insights, filterTarget, filterCategory, filterSentiment, oneYearAgo]
  );

  // Reset page when filters change — must be in useEffect, not useMemo
  useEffect(() => { setPage(0); }, [filterTarget, filterCategory, filterSentiment, oneYearAgo]);

  const paginated = useMemo(() => filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), [filtered, page]);
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const [diseaseFilter, setDiseaseFilter] = useState<string>("all");
  const { data: topics } = useQuery({
    queryKey: ["topics", period, diseaseFilter],
    queryFn: () => api.topics(period, diseaseFilter),
    refetchInterval: 30_000,
  });
  const { data: allTargets } = useQuery({ queryKey: ["targets"], queryFn: api.targets.list });
  const diseaseAreas = useMemo(() => {
    const areas = [...new Set((allTargets || []).map(t => t.disease_area).filter(Boolean))] as string[];
    return areas;
  }, [allTargets]);

  const { data: brief, isLoading: briefLoading } = useQuery({
    queryKey: ["daily-brief"],
    queryFn: () => api.dailyBrief(),
    staleTime: 6 * 60 * 60 * 1000,
    gcTime: 6 * 60 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const briefMut = useMutation({
    mutationFn: () => api.dailyBrief(true),
    onSuccess: (data) => { qc.setQueryData(["daily-brief"], data); qc.invalidateQueries({ queryKey: ["gen-quota"] }); },
  });
  const [selectedPoint, setSelectedPoint] = useState<string | null>(null);
  const [selectedSocialPoint, setSelectedSocialPoint] = useState<string | null>(null);

  const { data: kolBrief, isLoading: kolBriefLoading } = useQuery({
    queryKey: ["kol-brief"],
    queryFn: () => api.kolBrief(),
    staleTime: 6 * 60 * 60 * 1000,
    gcTime: 6 * 60 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const kolBriefMut = useMutation({
    mutationFn: () => api.kolBrief(true),
    onSuccess: (data) => { qc.setQueryData(["kol-brief"], data); qc.invalidateQueries({ queryKey: ["gen-quota"] }); },
  });

  const { data: socialBrief, isLoading: socialBriefLoading } = useQuery({
    queryKey: ["social-brief"],
    queryFn: () => api.socialBrief(),
    staleTime: 6 * 60 * 60 * 1000,
    gcTime: 6 * 60 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const socialBriefMut = useMutation({
    mutationFn: () => api.socialBrief(true),
    onSuccess: (data) => { qc.setQueryData(["social-brief"], data); qc.invalidateQueries({ queryKey: ["gen-quota"] }); },
  });

  // On-demand AI synthesis over the WHOLE DB (KOL + social) — always produces output
  const synthMut = useMutation({
    mutationFn: (refresh: boolean) => api.combinedSynthesis(refresh),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gen-quota"] }),
  });
  const synth = synthMut.data;

  // Competitor brief — same mechanism as the KOL brief, competitor targets only
  const { data: compBrief, isLoading: compBriefLoading } = useQuery({
    queryKey: ["competitor-brief"],
    queryFn: () => api.competitorBrief(),
    staleTime: 6 * 60 * 60 * 1000,
    gcTime: 6 * 60 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const compBriefMut = useMutation({
    mutationFn: () => api.competitorBrief(true),
    onSuccess: (data) => { qc.setQueryData(["competitor-brief"], data); qc.invalidateQueries({ queryKey: ["gen-quota"] }); },
  });

  // Global synthesis — merges KOL brief + population brief + burning-topic reports.
  // Poll every 3s only while a generation is running.
  const { data: globalSynth } = useQuery({
    queryKey: ["global-synthesis"],
    queryFn: api.reports.globalSynthesis,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });
  const globalSynthMut = useMutation({
    mutationFn: api.reports.triggerGlobalSynthesis,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-synthesis"] });
      qc.invalidateQueries({ queryKey: ["gen-quota"] });
    },
  });
  const gsRunning = globalSynth?.status === "running" || globalSynthMut.isPending;
  const gs = globalSynth?.result;

  // Daily AI-generation quota (1 regenerate/user/day; admins unlimited)
  const { can: canGen } = useGenQuota();

  // Collapsible brief cards — default collapsed to keep the dashboard compact
  const [openBriefs, setOpenBriefs] = useState<Record<string, boolean>>({});
  const toggleBrief = (k: string) => setOpenBriefs(s => ({ ...s, [k]: !s[k] }));

  const triggerMut = useMutation({
    mutationFn: () => api.runs.trigger(),
    onSuccess: (d) => { setActiveRunId(d.run_id); qc.invalidateQueries({ queryKey: ["current-run"] }); },
  });
  const stopMut = useMutation({
    mutationFn: api.runs.stop,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["current-run"] }),
  });

  const running = currentRun?.running;
  const progress = running && currentRun.total_targets
    ? Math.round(((currentRun.targets_processed ?? 0) / currentRun.total_targets) * 100)
    : 0;

  const hasTopics = topics && topics.total > 0;

  // Sentiment donut — dominant slice shown in the center
  const sentTotal = (topics?.sentiment ?? []).reduce((s, x) => s + x.count, 0);
  const dominantSent = sentTotal
    ? [...topics!.sentiment].sort((a, b) => b.count - a.count)[0]
    : null;
  const sentColor = (name: string) =>
    name === "Positive" ? "#22c55e" : name === "Negative" ? "#ef4444" : "#94a3b8";

  return (
    <>
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Welcome back, {greetName} </h1>
          <p className="text-sm text-gray-500 dark:text-[#94a3b8] mt-0.5">Here's your PharmaRadar intelligence overview.</p>
        </div>
        <div className="flex gap-2">
          {isAdmin && (running ? (
            <button onClick={() => stopMut.mutate()}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700">
              <Square size={14} /> Stop Run
            </button>
          ) : (
            <button onClick={() => triggerMut.mutate()} disabled={triggerMut.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light disabled:opacity-50">
              <Play size={14} /> Run Now
            </button>
          ))}
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard label="Active Targets" value={stats?.active_targets ?? "—"} icon={Users} />
        <StatCard label="Today's Insights" value={stats?.today_insights ?? "—"} icon={TrendingUp} />
        <StatCard label="Total Insights" value={stats?.total_insights ?? "—"} icon={FileText} />
        <StatCard label="Last Run" value={stats?.last_run_status ?? "Never"} icon={Clock}
          sub={stats?.last_run_at ? formatDateTime(stats.last_run_at) : undefined} />
      </div>

      {/* Active run progress */}
      {running && currentRun && (
        <div className="glass-panel rounded-xl p-5 border border-blue-200 dark:border-blue-900/50">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <RefreshCw size={16} className="animate-spin text-blue-500" />
              <span className="font-medium text-sm text-blue-900 dark:text-blue-100">
                Pipeline running — {currentRun.current_target ?? "initialising..."}
              </span>
            </div>
            <span className="text-sm text-blue-700 dark:text-blue-300 font-medium">{currentRun.targets_processed}/{currentRun.total_targets} targets</span>
          </div>
          <div className="w-full bg-slate-200/50 dark:bg-slate-800/50 rounded-full h-2 overflow-hidden shadow-inner">
            <div className="bg-gradient-to-r from-blue-400 to-indigo-500 h-2 rounded-full transition-all duration-500 relative" style={{ width: `${progress}%` }}>
              <div className="absolute inset-0 bg-white/20 animate-pulse" />
            </div>
          </div>
          <div className="flex gap-4 mt-3 text-xs font-medium text-slate-500 dark:text-slate-400">
            <span>{currentRun.new_posts_found} new posts</span>
            <span>{currentRun.insights_extracted} insights</span>
            <span>{currentRun.llm_calls_used} LLM calls</span>
          </div>
        </div>
      )}

      {/* Intelligence Analytics */}
      <div className="glass rounded-xl">
        <div className="flex flex-wrap items-center justify-between gap-2 px-6 pt-6 pb-4 border-b border-slate-200/50 dark:border-slate-800/50">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg">
              <BarChart2 size={18} className="text-indigo-600 dark:text-indigo-400 shrink-0" />
            </div>
            <h2 className="font-semibold text-sm whitespace-nowrap">Intelligence Analytics</h2>
          </div>
          <div className="flex gap-1">
            {PERIOD_OPTIONS.map((o) => (
              <button key={o.value} onClick={() => setPeriod(o.value)}
                className={cn(
                  "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors whitespace-nowrap",
                  period === o.value
                    ? "bg-pharma-blue text-white"
                    : "text-gray-500 hover:text-pharma-light"
                )}>
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {!hasTopics ? (
          <div className="text-center py-12 text-gray-400 text-sm">
            No insights yet — run the pipeline to populate analytics.
          </div>
        ) : (
          <div className="p-5 grid grid-cols-1 lg:grid-cols-3 gap-6">

            {/* Category bar chart */}
            <div className="lg:col-span-2">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                Most Discussed Topics — last {period} days
                <span className="ml-2 text-pharma-light font-bold">{topics.total} insights</span>
              </p>
              {/* onMouseDown preventDefault stops the SVG getting focus (black border) */}
              <div onMouseDown={e => e.preventDefault()}>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={topics.categories} layout="vertical"
                    margin={{ left: 0, right: 16, top: 0, bottom: 0 }}>
                    <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                    <Tooltip
                      cursor={{ fill: 'rgba(148,163,184,0.12)', radius: 4 }}
                      contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderRadius: '8px', border: 'none', color: '#f1f5f9', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                      itemStyle={{ color: '#e2e8f0' }}
                      formatter={(v) => [`${v} insights`, ""]}
                    />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]} cursor="pointer"
                      onClick={(data) => {
                        if (data?.name) setChartPanel({ label: data.name, type: "category", value: data.name });
                      }}
                      animationDuration={1000}
                    >
                      {topics.categories.map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Sentiment donut */}
            <div>
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-4">Sentiment</p>
              <div onMouseDown={e => e.preventDefault()} className="relative">
                <ResponsiveContainer width="100%" height={168}>
                  <PieChart>
                    <Pie data={topics.sentiment.filter(s => s.count > 0)} dataKey="count" nameKey="name"
                      cx="50%" cy="50%" innerRadius={58} outerRadius={78}
                      cursor="pointer"
                      paddingAngle={topics.sentiment.filter(s => s.count > 0).length > 1 ? 4 : 0}
                      cornerRadius={8}
                      stroke="none"
                      labelLine={false}
                      animationDuration={800}
                      onClick={(entry) => {
                        if (entry?.name) setChartPanel({ label: entry.name, type: "sentiment", value: entry.name });
                      }}>
                      {topics.sentiment.filter(s => s.count > 0).map((entry) => (
                        <Cell key={entry.name} fill={sentColor(entry.name)} />
                      ))}
                    </Pie>
                    <Tooltip
                      position={{ y: 0 }}
                      wrapperStyle={{ zIndex: 20 }}
                      contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.92)', borderRadius: '8px', border: 'none', color: '#f1f5f9', fontSize: 12, padding: '4px 8px', boxShadow: '0 8px 24px -6px rgba(0,0,0,0.35)' }}
                      formatter={(v) => [`${v} insights — click to view`, ""]}
                    />
                  </PieChart>
                </ResponsiveContainer>
                {dominantSent && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <span className="text-[26px] font-bold leading-none tabular-nums text-slate-900 dark:text-slate-100">
                      {Math.round((dominantSent.count / sentTotal) * 100)}%
                    </span>
                    <span className="text-[11px] font-medium mt-1" style={{ color: sentColor(dominantSent.name) }}>{dominantSent.name}</span>
                  </div>
                )}
              </div>
              {/* breakdown list */}
              <div className="mt-5 space-y-2.5">
                {topics.sentiment.filter(s => s.count > 0).map((s) => {
                  const pct = sentTotal ? Math.round((s.count / sentTotal) * 100) : 0;
                  return (
                    <button key={s.name}
                      onClick={() => setChartPanel({ label: s.name, type: "sentiment", value: s.name })}
                      className="flex items-center gap-2.5 w-full group cursor-pointer rounded-lg px-2 py-1.5 -mx-2 hover:bg-slate-100 dark:hover:bg-slate-800/60 transition-colors">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: sentColor(s.name) }} />
                      <span className="text-[13px] text-slate-600 dark:text-slate-300 group-hover:text-slate-900 dark:group-hover:text-white transition-colors">{s.name}</span>
                      <span className="ml-auto text-[12px] text-slate-400 tabular-nums">{pct}%</span>
                      <span className="text-[13px] font-semibold text-slate-900 dark:text-slate-100 tabular-nums w-6 text-right">{s.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Top KOLs bar */}
            {topics.top_kols.length > 0 && (
              <div className="lg:col-span-2">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Top KOLs by insights</p>
                <div onMouseDown={e => e.preventDefault()}>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={topics.top_kols} layout="vertical"
                      margin={{ left: 0, right: 16, top: 0, bottom: 0 }}>
                      <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                      <YAxis type="category" dataKey="name" width={150} tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                      <Tooltip
                        cursor={{ fill: 'rgba(148,163,184,0.12)', radius: 4 }}
                        contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderRadius: '8px', border: 'none', color: '#f1f5f9' }}
                        formatter={(v) => [`${v} insights`, ""]}
                      />
                      <Bar dataKey="count" fill={PHARMA_BLUE} radius={[0, 4, 4, 0]} cursor="pointer"
                        animationDuration={1000}
                        onClick={(data) => {
                          if (data?.name) setChartPanel({ label: data.name, type: "kol", value: data.name });
                        }} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* Top topics list */}
            {topics.top_topics.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Trending topics</p>
                  {diseaseAreas.length > 0 && (
                    <select
                      value={diseaseFilter}
                      onChange={e => setDiseaseFilter(e.target.value)}
                      className="text-xs border border-gray-200 dark:border-[#1e3a5f] rounded px-2 py-0.5 bg-transparent text-gray-600 dark:text-[#94a3b8]"
                    >
                      <option value="all">All areas</option>
                      {diseaseAreas.map(a => <option key={a} value={a}>{a}</option>)}
                    </select>
                  )}
                </div>
                <div className="space-y-2">
                  {topics.top_topics.slice(0, 8).map((t) => {
                    const maxScore = topics.top_topics[0].trend_score || 1;
                    const barWidth = Math.max(4, (t.trend_score / maxScore) * 100);
                    return (
                      <div key={t.topic}
                        className="flex items-center gap-2 cursor-pointer group rounded-lg px-2 py-1.5 -mx-2 hover:bg-slate-100 dark:hover:bg-slate-800/60 transition-colors"
                        onClick={() => setChartPanel({ label: t.topic, type: "topic", value: t.topic })}>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs text-gray-700 dark:text-[#94a3b8] truncate group-hover:text-pharma-light transition-colors" title={t.topic}>
                            {t.topic}
                          </div>
                          <div className="h-1.5 bg-gray-100 dark:bg-[#1e2d4a] rounded-full mt-1">
                            <div className="h-1.5 bg-pharma-light rounded-full group-hover:bg-pharma-blue transition-colors"
                              style={{ width: `${barWidth}%` }} />
                          </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          {t.likes > 0 && <span className="text-[10px] text-gray-400">♥{t.likes > 999 ? `${(t.likes/1000).toFixed(1)}k` : t.likes}</span>}
                          <span className="text-xs font-semibold text-pharma-light">{t.count}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Social trending analytics (compact) → full page at /social */}
      <SocialTrendsSummary />

      {/* Daily Brief */}
      <div className="glass rounded-xl p-5">
        <div className={cn("flex items-center justify-between", openBriefs.daily && "mb-4")}
          onClick={() => toggleBrief("daily")}>
          <div className="flex items-center gap-2 cursor-pointer">
            {openBriefs.daily ? <ChevronDown size={15} className="text-gray-400 shrink-0"/> : <ChevronRight size={15} className="text-gray-400 shrink-0"/>}
            <div className="p-2 bg-amber-50 dark:bg-amber-900/20 rounded-lg">
              <Sparkles size={16} className="text-amber-600 dark:text-amber-400"/>
            </div>
            <div>
              <h2 className="font-semibold text-sm">Today's Intelligence Brief</h2>
              <p className="text-xs text-gray-400">AI-generated key takeaways from KOL monitoring + social trends</p>
            </div>
          </div>
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            {brief && brief.kol_count > 0 && (
              <span className="text-[10px] text-gray-400">{brief.kol_count} insights · {brief.social_count} posts</span>
            )}
            {canGen("daily_brief") && (
              <button onClick={() => briefMut.mutate()} disabled={briefMut.isPending || briefLoading}
                title="Regenerate brief from latest DB data (1 per day)"
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-amber-300 dark:border-amber-800 text-amber-600 dark:text-amber-400 rounded-lg hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-50 transition-colors">
                {briefMut.isPending ? <Loader2 size={11} className="animate-spin"/> : <RefreshCw size={11}/>}
                Regenerate
              </button>
            )}
          </div>
        </div>
        {openBriefs.daily && (briefLoading || briefMut.isPending ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin"/>Generating brief…
          </div>
        ) : brief && brief.points.length > 0 ? (
          <div className="space-y-2">
            {brief.points.map((p: DailyBriefPoint, i: number) => (
              <button key={i} onClick={() => setSelectedPoint(p.text)}
                className={cn(
                  "w-full text-left flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all hover:shadow-sm group",
                  p.priority === "high"
                    ? "bg-amber-50/60 dark:bg-amber-900/10 border-amber-200/60 dark:border-amber-800/20 hover:border-amber-400/50"
                    : "bg-gray-50/60 dark:bg-[#0d1424]/40 border-slate-200/50 dark:border-white/5 hover:border-pharma-blue/30"
                )}>
                <span className={cn("w-1.5 h-1.5 rounded-full mt-1.5 shrink-0",
                  p.priority === "high" ? "bg-amber-500" : "bg-gray-300 dark:bg-slate-600")}/>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{p.text}</p>
                  <span className={cn("text-[10px] font-semibold mt-0.5 inline-block",
                    p.source === "kol" ? "text-blue-500" : p.source === "social" ? "text-orange-500" : "text-purple-500")}>
                    {p.source === "kol" ? "KOL insight" : p.source === "social" ? "Social trend" : "KOL + Social"}
                  </span>
                </div>
                <ChevronRight size={14} className="text-gray-300 group-hover:text-pharma-blue shrink-0 mt-0.5 transition-colors"/>
              </button>
            ))}
          </div>
        ) : brief?.error ? (
          <p className="text-xs text-red-400">LLM error: {brief.error}</p>
        ) : (
          <p className="text-sm text-gray-400">
            {brief && (brief.kol_count > 0 || brief.social_count > 0)
              ? "Click Generate to create the brief from existing data."
              : "No data yet — run a pipeline first, then click Generate."}
          </p>
        ))}
      </div>

      {/* AI Synthesis & takeaway — whole-DB (KOL + social) */}
      <SynthesisPanel
        collapsible
        defaultCollapsed
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

      {/* Social Trends Brief */}
      <div className="glass rounded-xl p-5">
        <div className={cn("flex items-center justify-between", openBriefs.social && "mb-4")}
          onClick={() => toggleBrief("social")}>
          <div className="flex items-center gap-2 cursor-pointer">
            {openBriefs.social ? <ChevronDown size={15} className="text-gray-400 shrink-0"/> : <ChevronRight size={15} className="text-gray-400 shrink-0"/>}
            <div className="p-2 bg-orange-50 dark:bg-orange-900/20 rounded-lg">
              <TrendingUp size={16} className="text-orange-500 dark:text-orange-400"/>
            </div>
            <div>
              <h2 className="font-semibold text-sm">Social Trends Brief</h2>
              <p className="text-xs text-gray-400">Sector-grouped signals from social media — 6-month window</p>
            </div>
          </div>
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            {socialBrief && socialBrief.total_posts > 0 && (
              <span className="text-[10px] text-gray-400">{socialBrief.total_posts} posts · {socialBrief.sections?.length ?? 0} sectors</span>
            )}
            {canGen("social_brief") && (
              <button onClick={() => socialBriefMut.mutate()} disabled={socialBriefMut.isPending || socialBriefLoading}
                title="Regenerate (1 per day)"
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-orange-300 dark:border-orange-800 text-orange-600 dark:text-orange-400 rounded-lg hover:bg-orange-50 dark:hover:bg-orange-900/20 disabled:opacity-50 transition-colors">
                {socialBriefMut.isPending ? <Loader2 size={11} className="animate-spin"/> : <RefreshCw size={11}/>}
                Regenerate
              </button>
            )}
          </div>
        </div>

        {openBriefs.social && (<>
        {/* Top topics chips */}
        {socialBrief?.top_topics && socialBrief.top_topics.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            {socialBrief.top_topics.slice(0,8).map((t, i) => (
              <span key={i} className="px-2 py-0.5 text-[10px] font-medium bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 rounded-full border border-orange-200/50 dark:border-orange-800/30">
                {t.topic} <span className="opacity-60">({t.count})</span>
              </span>
            ))}
          </div>
        )}

        {socialBriefLoading || socialBriefMut.isPending ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin"/>Analysing {socialBrief?.total_posts ?? ""} posts across sectors…
          </div>
        ) : socialBrief?.sections && socialBrief.sections.length > 0 ? (
          <div className="space-y-4">
            {socialBrief.sections.map((sec, si) => (
              <div key={si}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-orange-500">{sec.sector}</span>
                  {sec.key_signal && <span className="text-[10px] text-gray-400 italic">— {sec.key_signal}</span>}
                </div>
                <div className="space-y-1.5 pl-2 border-l-2 border-orange-200/50 dark:border-orange-800/30">
                  {sec.points.map((p, i) => (
                    <button key={i} onClick={() => setSelectedSocialPoint(p.text)}
                      className="w-full text-left flex items-start gap-3 p-2.5 rounded-lg border border-slate-200/50 dark:border-white/5 bg-gray-50/40 dark:bg-[#0d1424]/30 hover:border-orange-300/50 hover:shadow-sm transition-all group cursor-pointer">
                      <span className={cn("w-1.5 h-1.5 rounded-full mt-1.5 shrink-0", p.priority === "high" ? "bg-orange-500" : "bg-gray-300 dark:bg-slate-600")}/>
                      <p className="flex-1 text-sm text-gray-700 dark:text-[#e2e8f0]">{p.text}</p>
                      <ChevronRight size={13} className="text-gray-300 group-hover:text-orange-500 shrink-0 mt-0.5 transition-colors"/>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : socialBrief?.error ? (
          <p className="text-xs text-red-400">Error: {socialBrief.error}</p>
        ) : (
          <p className="text-sm text-gray-400">
            {socialBrief && socialBrief.total_posts > 0
              ? "Click Generate to analyse social trends by sector."
              : "No social posts yet — run a social scan first."}
          </p>
        )}
        </>)}
      </div>

      {/* KOL Intelligence Brief */}
      <div className="glass rounded-xl p-5">
        <div className={cn("flex items-center justify-between", openBriefs.kol && "mb-4")}
          onClick={() => toggleBrief("kol")}>
          <div className="flex items-center gap-2 cursor-pointer">
            {openBriefs.kol ? <ChevronDown size={15} className="text-gray-400 shrink-0"/> : <ChevronRight size={15} className="text-gray-400 shrink-0"/>}
            <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
              <Users size={16} className="text-blue-600 dark:text-blue-400"/>
            </div>
            <div>
              <h2 className="font-semibold text-sm">KOL Intelligence Brief</h2>
              <p className="text-xs text-gray-400">Key insights from monitored KOL statements — last 6 months</p>
            </div>
          </div>
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            {kolBrief && kolBrief.kol_count > 0 && (
              <span className="text-[10px] text-gray-400">{kolBrief.kol_count} insights analysed</span>
            )}
            {canGen("kol_brief") && (
              <button onClick={() => kolBriefMut.mutate()} disabled={kolBriefMut.isPending || kolBriefLoading}
                title="Regenerate (1 per day)"
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-blue-300 dark:border-blue-800 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50 transition-colors">
                {kolBriefMut.isPending ? <Loader2 size={11} className="animate-spin"/> : <RefreshCw size={11}/>}
                Regenerate
              </button>
            )}
          </div>
        </div>
        {openBriefs.kol && (kolBriefLoading || kolBriefMut.isPending ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin"/>Analysing KOL insights…
          </div>
        ) : kolBrief && kolBrief.points.length > 0 ? (
          <div className="space-y-2">
            {kolBrief.points.map((p, i) => (
              <button key={i} onClick={() => setSelectedPoint(p.text)}
                className={cn(
                  "w-full text-left flex items-start gap-3 p-3 rounded-xl border transition-all group cursor-pointer hover:shadow-sm",
                  p.priority === "high"
                    ? "bg-amber-50/60 dark:bg-amber-900/10 border-amber-200/60 dark:border-amber-800/20 hover:border-amber-400/50"
                    : "bg-gray-50/60 dark:bg-[#0d1424]/40 border-slate-200/50 dark:border-white/5 hover:border-blue-300/50"
                )}>
                <span className={cn("w-1.5 h-1.5 rounded-full mt-1.5 shrink-0", p.priority === "high" ? "bg-amber-500" : "bg-blue-400")}/>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{p.text}</p>
                  <span className="text-[10px] font-semibold mt-0.5 inline-block text-blue-500">KOL insight</span>
                </div>
                <ChevronRight size={14} className="text-gray-300 group-hover:text-blue-500 shrink-0 mt-0.5 transition-colors"/>
              </button>
            ))}
          </div>
        ) : kolBrief?.error ? (
          <p className="text-xs text-red-400">Error: {kolBrief.error}</p>
        ) : (
          <p className="text-sm text-gray-400">
            {kolBrief && kolBrief.kol_count > 0 ? "Click Generate to create KOL brief." : "No KOL insights yet — run a pipeline first."}
          </p>
        ))}
      </div>

      {/* Competitor Intelligence Brief */}
      <div className="glass rounded-xl p-5">
        <div className={cn("flex items-center justify-between", openBriefs.competitor && "mb-4")}
          onClick={() => toggleBrief("competitor")}>
          <div className="flex items-center gap-2 cursor-pointer">
            {openBriefs.competitor ? <ChevronDown size={15} className="text-gray-400 shrink-0"/> : <ChevronRight size={15} className="text-gray-400 shrink-0"/>}
            <div className="p-2 bg-orange-50 dark:bg-orange-900/20 rounded-lg">
              <BarChart2 size={16} className="text-orange-600 dark:text-orange-400"/>
            </div>
            <div>
              <h2 className="font-semibold text-sm">Competitor Brief</h2>
              <p className="text-xs text-gray-400">What rival companies are launching and signalling — last 6 months</p>
            </div>
          </div>
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            {compBrief && compBrief.kol_count > 0 && (
              <span className="text-[10px] text-gray-400">{compBrief.kol_count} publications analysed</span>
            )}
            {canGen("competitor_brief") && (
              <button onClick={() => compBriefMut.mutate()} disabled={compBriefMut.isPending || compBriefLoading}
                title="Regenerate (1 per day)"
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-orange-300 dark:border-orange-800 text-orange-600 dark:text-orange-400 rounded-lg hover:bg-orange-50 dark:hover:bg-orange-900/20 disabled:opacity-50 transition-colors">
                {compBriefMut.isPending ? <Loader2 size={11} className="animate-spin"/> : <RefreshCw size={11}/>}
                Regenerate
              </button>
            )}
          </div>
        </div>
        {openBriefs.competitor && (compBriefLoading || compBriefMut.isPending ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin"/>Analysing competitor content…
          </div>
        ) : compBrief && compBrief.points.length > 0 ? (
          <div className="space-y-2">
            {compBrief.points.map((p, i) => (
              <div key={i} className="flex items-start gap-3 p-3 rounded-xl border bg-orange-50/40 dark:bg-orange-900/5 border-orange-200/50 dark:border-orange-800/20">
                <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 bg-orange-500"/>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{p.text}</p>
                  <span className="text-[10px] font-semibold mt-0.5 inline-block text-orange-500">Competitor signal</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">
            {compBrief?.error || "No competitor content yet — add competitor targets and run a scrape."}
          </p>
        ))}
      </div>

      {/* Global Synthesis — KOL + population + burning topics in one report */}
      <div className="glass rounded-xl p-5">
        <div className={cn("flex items-center justify-between", openBriefs.global && "mb-4")}
          onClick={() => toggleBrief("global")}>
          <div className="flex items-center gap-2 cursor-pointer">
            {openBriefs.global ? <ChevronDown size={15} className="text-gray-400 shrink-0"/> : <ChevronRight size={15} className="text-gray-400 shrink-0"/>}
            <div className="p-2 bg-purple-50 dark:bg-purple-900/20 rounded-lg">
              <Sparkles size={16} className="text-purple-600 dark:text-purple-400"/>
            </div>
            <div>
              <h2 className="font-semibold text-sm">Global Synthesis</h2>
              <p className="text-xs text-gray-400">
                One report merging the KOL brief, all-population brief and burning-topic reports
                {gs?.generated_at ? ` — last generated ${formatDateTime(gs.generated_at)}` : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            {gs?.pdf_url && (
              <button onClick={() => api.reports.openPdf(gs.pdf_url!).catch(() => alert("PDF not available"))}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-purple-300 dark:border-purple-800 text-purple-600 dark:text-purple-400 rounded-lg hover:bg-purple-50 dark:hover:bg-purple-900/20 transition-colors">
                <FileText size={11}/> PDF
              </button>
            )}
            {canGen("global_synthesis") && (
              <button onClick={() => { setOpenBriefs(s => ({ ...s, global: true })); globalSynthMut.mutate(); }} disabled={gsRunning}
                title="Generate (1 per day)"
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs border border-purple-300 dark:border-purple-800 text-purple-600 dark:text-purple-400 rounded-lg hover:bg-purple-50 dark:hover:bg-purple-900/20 disabled:opacity-50 transition-colors">
                {gsRunning ? <Loader2 size={11} className="animate-spin"/> : <Sparkles size={11}/>}
                {gsRunning ? "Generating…" : gs ? "Regenerate" : "Generate"}
              </button>
            )}
          </div>
        </div>
        {openBriefs.global && (
          gsRunning && !gs ? (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Loader2 size={14} className="animate-spin"/>Merging the three report layers…
            </div>
          ) : globalSynth?.status === "failed" && !gs ? (
            <p className="text-xs text-red-400">Error: {globalSynth.error || "generation failed"}</p>
          ) : gs ? (
            <div className="space-y-4">
              {gsRunning && (
                <div className="flex items-center gap-2 text-xs text-purple-500">
                  <Loader2 size={12} className="animate-spin"/>A fresh synthesis is generating — showing the previous one meanwhile.
                </div>
              )}
              {gs.exec_summary && (
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] whitespace-pre-wrap leading-relaxed">{gs.exec_summary}</p>
              )}
              <div className="grid md:grid-cols-3 gap-3">
                {([
                  ["KOL takeaways", gs.kol_takeaways],
                  ["All-population takeaways", gs.population_takeaways],
                  ["Burning-topic takeaways", gs.topic_takeaways],
                ] as const).map(([label, items]) => (
                  <div key={label} className="rounded-lg border border-slate-200/60 dark:border-white/10 p-3">
                    <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">{label}</div>
                    {items.length > 0 ? (
                      <ul className="space-y-1">
                        {items.map((t, i) => (
                          <li key={i} className="text-xs text-gray-600 dark:text-gray-300 flex gap-1.5">
                            <span className="text-purple-400 shrink-0">•</span>{t}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-gray-400">No data this period.</p>
                    )}
                  </div>
                ))}
              </div>
              {gs.so_what && (
                <div className="rounded-lg bg-purple-50/60 dark:bg-purple-900/10 border border-purple-100 dark:border-purple-900/30 px-4 py-3">
                  <div className="text-[10px] font-semibold text-purple-700 dark:text-purple-300 uppercase tracking-wider mb-1">So what</div>
                  <p className="text-sm text-gray-700 dark:text-[#e2e8f0] whitespace-pre-wrap">{gs.so_what}</p>
                </div>
              )}
              {gs.important_posts.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Important posts</div>
                  {gs.important_posts.map((p, i) => (
                    <a key={i} href={p.url} target="_blank" rel="noreferrer"
                      className="block text-xs text-gray-600 dark:text-gray-300 hover:text-purple-600 truncate">
                      <ExternalLink size={10} className="inline mr-1 text-gray-300"/>
                      {(p.title || p.url)}{p.why ? ` — ${p.why}` : ""}
                    </a>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              Generates one merged synthesis from the latest KOL brief, all-population brief and every
              active burning topic's latest report. Generate those first for the richest result.
            </p>
          )
        )}
      </div>


      {/* Recent insights feed */}
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <h2 className="text-lg font-semibold mr-1">Recent Findings</h2>
          <Filter size={13} className="text-gray-400 shrink-0" />

          {/* Target filter */}
          <select
            value={filterTarget}
            onChange={e => setFilterTarget(e.target.value)}
            className="text-xs border border-gray-200 dark:border-slate-800 rounded-lg px-2 py-1 bg-white dark:bg-slate-900 text-gray-600 dark:text-[#94a3b8]"
          >
            <option value="">All KOLs</option>
            {targets.map(t => <option key={t} value={t}>{t}</option>)}
          </select>

          {/* Category filter */}
          <select
            value={filterCategory}
            onChange={e => setFilterCategory(e.target.value)}
            className="text-xs border border-gray-200 dark:border-slate-800 rounded-lg px-2 py-1 bg-white dark:bg-slate-900 text-gray-600 dark:text-[#94a3b8]"
          >
            <option value="">All categories</option>
            {categories.map(c => <option key={c} value={c}>{c?.replace(/_/g, " ")}</option>)}
          </select>

          {/* Sentiment filter */}
          {["positive", "neutral", "negative"].map(s => (
            <button
              key={s}
              onClick={() => setFilterSentiment(f => f === s ? "" : s)}
              className={cn(
                "text-xs px-2 py-1 rounded-full border transition-colors",
                filterSentiment === s
                  ? SENTIMENT_COLORS[s] + " border-transparent font-medium"
                  : "border-gray-200 dark:border-[#1e3a5f] text-gray-500 hover:border-gray-300"
              )}
            >
              {s}
            </button>
          ))}

          {(filterTarget || filterCategory || filterSentiment) && (
            <button
              onClick={() => { setFilterTarget(""); setFilterCategory(""); setFilterSentiment(""); }}
              className="text-xs text-gray-400 hover:text-gray-600 underline"
            >
              clear
            </button>
          )}
          {insights?.length ? (
            <span className="text-xs text-gray-400 ml-auto">{filtered.length} / {insights.length}</span>
          ) : null}
        </div>

        <div className="space-y-3">
          {paginated.map((ins) => <InsightCard key={ins.id} insight={ins} onClick={() => setDrawerInsight(ins)} />)}
          {!filtered.length && (
            <div className="text-center py-12 text-gray-400">
              {insights?.length ? "No results match the filters." : "No insights yet — run the pipeline to collect data."}
            </div>
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between pt-3 border-t border-gray-100 dark:border-[#1e3a5f]/50">
            <button
              onClick={() => setPage(p => p - 1)}
              disabled={page === 0}
              className="px-3 py-1.5 text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light hover:text-pharma-light disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              ← Prev
            </button>
            <span className="text-xs text-gray-400">Page {page + 1} of {totalPages}</span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages - 1}
              className="px-3 py-1.5 text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light hover:text-pharma-light disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
    {/* Chart click → insight list panel */}
    {chartPanel && (
      <div className="fixed inset-0 z-50 flex">
        <div className="flex-1 bg-black/30" onClick={() => setChartPanel(null)} />
        <div className="w-full max-w-lg bg-white dark:bg-slate-900 shadow-2xl flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-slate-800 shrink-0">
            <div>
              <p className="text-xs text-gray-400 dark:text-[#64748b] capitalize mb-0.5">{chartPanel.type === "kol" ? "KOL" : chartPanel.type}</p>
              <h2 className="font-bold text-gray-900 dark:text-[#e2e8f0] text-base leading-snug">{chartPanel.label}</h2>
              <p className="text-xs text-gray-400 mt-0.5">{chartPanelInsights.length} insight{chartPanelInsights.length !== 1 ? "s" : ""}</p>
            </div>
            <button onClick={() => setChartPanel(null)} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/30 transition-all">
              <X size={18} />
            </button>
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {chartPanelInsights.length === 0 ? (
              <div className="text-center py-12 text-gray-400 text-sm">No insights found for this selection.</div>
            ) : (
              chartPanelInsights.map(ins => (
                <div key={ins.id}
                  onClick={() => { setDrawerInsight(ins); setChartPanel(null); }}
                  className="bg-gray-50 dark:bg-slate-800/50 rounded-xl p-4 border border-gray-100 dark:border-slate-700/50 cursor-pointer hover:border-pharma-light/40 hover:shadow-sm transition-all">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="text-xs font-semibold text-pharma-light">{ins.target_name}</span>
                        {ins.category && <span className="text-xs text-gray-400 dark:text-[#64748b]">{ins.category.replace(/_/g," ")}</span>}
                      </div>
                      <p className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0] leading-snug">{ins.topic}</p>
                    </div>
                    <span className={cn("text-xs px-2 py-0.5 rounded-full shrink-0 font-medium border",
                      ins.sentiment === "positive" ? "bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800/30" :
                      ins.sentiment === "negative" ? "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800/30" :
                      "bg-gray-100 text-gray-600 border-gray-200 dark:bg-[#1e3a5f]/30 dark:text-[#94a3b8] dark:border-[#1e3a5f]"
                    )}>{ins.sentiment}</span>
                  </div>
                  <p className="text-sm text-gray-600 dark:text-[#64748b] line-clamp-2 leading-relaxed">{ins.what_they_said}</p>
                  {ins.published_date && (
                    <p className="text-xs text-gray-400 dark:text-[#475569] mt-2">{ins.published_date}</p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    )}

    {/* Insight Drawer */}
    {drawerInsight && (
      <div className="fixed inset-0 z-50 flex">
        <div className="flex-1 bg-black/30" onClick={() => setDrawerInsight(null)} />
        <div className="w-full max-w-md bg-white dark:bg-slate-900 shadow-2xl flex flex-col h-full overflow-y-auto">
          {/* Drawer header */}
          <div className="flex items-start justify-between p-5 border-b border-gray-100 dark:border-slate-800">
            <div>
              <p className="text-xs font-medium text-pharma-light mb-1">{drawerInsight.target_name}</p>
              <h2 className="font-semibold text-gray-900 dark:text-[#e2e8f0] text-base leading-snug">{drawerInsight.topic}</h2>
            </div>
            <button onClick={() => setDrawerInsight(null)} className="ml-4 text-gray-400 hover:text-gray-600 shrink-0">
              <X size={20} />
            </button>
          </div>

          {/* Drawer body */}
          <div className="p-5 space-y-5 flex-1">
            {/* Sentiment + category */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className={cn("text-xs px-2.5 py-1 rounded-full font-medium", SENTIMENT_COLORS[drawerInsight.sentiment] ?? SENTIMENT_COLORS.neutral)}>
                {drawerInsight.sentiment}
              </span>
              {drawerInsight.category && (
                <span className="text-xs px-2.5 py-1 rounded-full bg-gray-100 dark:bg-[#1e3a5f]/40 text-gray-600 dark:text-[#94a3b8]">
                  {drawerInsight.category.replace(/_/g, " ")}
                </span>
              )}
            </div>

            {/* What they said */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">What they said</p>
              <p className="text-sm text-gray-800 dark:text-[#e2e8f0] leading-relaxed">{drawerInsight.what_they_said}</p>
            </div>

            {/* Context */}
            {drawerInsight.context && (
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Context</p>
                <p className="text-sm text-gray-600 dark:text-[#94a3b8] leading-relaxed">{drawerInsight.context}</p>
              </div>
            )}

            {/* Meta */}
            <div className="pt-3 border-t border-gray-100 dark:border-[#1e3a5f]/50 space-y-2">
              {drawerInsight.published_date && (
                <div className="flex justify-between text-xs">
                  <span className="text-gray-400">Published</span>
                  <span className="text-gray-600 dark:text-[#94a3b8]">{drawerInsight.published_date}</span>
                </div>
              )}
              {drawerInsight.source_name && (
                <div className="flex justify-between text-xs">
                  <span className="text-gray-400">Source</span>
                  <span className="text-gray-600 dark:text-[#94a3b8]">{drawerInsight.source_name}</span>
                </div>
              )}
            </div>

            {/* Source link */}
            {drawerInsight.source_url && (
              <a
                href={drawerInsight.source_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-center gap-2 w-full py-2.5 border border-pharma-light text-pharma-light rounded-lg text-sm font-medium hover:bg-pharma-light hover:text-white transition-colors"
              >
                <ExternalLink size={14} /> View Original Post
              </a>
            )}
          </div>
        </div>
      </div>
    )}
    <>
      {selectedPoint && (
        <BriefDetailModal point={selectedPoint} onClose={() => setSelectedPoint(null)}/>
      )}
      {selectedSocialPoint && (
        <SocialDetailModal point={selectedSocialPoint} onClose={() => setSelectedSocialPoint(null)}/>
      )}
    </>
    </>
  );
}

function BriefDetailModal({ point, onClose }: { point: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["brief-detail", point],
    queryFn: () => api.briefDetail(point),
    staleTime: Infinity,
  });

  const SENT_COLOR: Record<string, string> = {
    positive: "text-emerald-600 dark:text-emerald-400",
    negative: "text-red-500 dark:text-red-400",
    neutral:  "text-gray-500 dark:text-slate-400",
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose}/>
      <div className="relative w-full max-w-lg bg-white dark:bg-[#0d1424] border-l border-slate-200 dark:border-white/10 flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-slate-200/50 dark:border-white/10 flex-none">
          <div className="p-2 bg-amber-50 dark:bg-amber-900/20 rounded-lg shrink-0 mt-0.5">
            <Sparkles size={14} className="text-amber-600 dark:text-amber-400"/>
          </div>
          <p className="flex-1 text-sm font-semibold text-gray-900 dark:text-white leading-snug">{point}</p>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/40 rounded-lg text-gray-400 shrink-0">
            <X size={16}/>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {isLoading ? (
            <div className="space-y-3">
              {[85,60,75,50].map((w,i) => <div key={i} className="h-4 rounded bg-gray-100 dark:bg-[#1e3a5f]/40 animate-pulse" style={{width:`${w}%`}}/>)}
            </div>
          ) : data ? (
            <>
              {/* Summary */}
              <div>
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">Summary</p>
                <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.summary}</p>
              </div>

              {/* So what */}
              {data.so_what && (
                <div className="bg-pharma-blue/5 dark:bg-pharma-blue/10 rounded-xl p-4 border border-pharma-blue/20">
                  <p className="text-[10px] font-black uppercase tracking-widest text-pharma-blue mb-2">So what for Roche?</p>
                  <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.so_what}</p>
                </div>
              )}

              {/* Action */}
              {data.action && (
                <div className="bg-amber-50 dark:bg-amber-900/10 rounded-xl p-4 border border-amber-200/50 dark:border-amber-800/20">
                  <p className="text-[10px] font-black uppercase tracking-widest text-amber-600 mb-2">Recommended Action</p>
                  <p className="text-sm text-gray-700 dark:text-[#cbd5e1]">{data.action}</p>
                </div>
              )}

              {/* KOL evidence */}
              {data.kol_insights.length > 0 && (
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">KOL Evidence</p>
                  <div className="space-y-2">
                    {data.kol_insights.map((k, i) => (
                      <div key={i} className="glass-panel rounded-lg p-3 border border-slate-200/50 dark:border-white/5">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-semibold text-pharma-blue">{k.kol}</span>
                          {k.topic && <span className="text-[10px] text-gray-400">· {k.topic}</span>}
                          {k.sentiment && <span className={cn("text-[10px] font-medium ml-auto", SENT_COLOR[k.sentiment] || SENT_COLOR.neutral)}>{k.sentiment}</span>}
                        </div>
                        <p className="text-xs text-gray-600 dark:text-[#94a3b8] italic">"{k.said}"</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Social evidence */}
              {data.social_posts.length > 0 && (
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">Social Evidence</p>
                  <div className="space-y-2">
                    {data.social_posts.map((s, i) => (
                      <a key={i} href={s.url} target="_blank" rel="noreferrer"
                        className="flex items-start gap-2 glass-panel rounded-lg p-3 border border-slate-200/50 dark:border-white/5 hover:border-orange-300/50 transition-colors group">
                        <span className="text-[10px] font-bold text-orange-500 uppercase shrink-0 mt-0.5">{s.platform}</span>
                        <p className="text-xs text-gray-600 dark:text-[#94a3b8] flex-1 line-clamp-2">{s.text}</p>
                        <span className="text-[10px] text-gray-400 shrink-0">♥ {s.likes}</span>
                      </a>
                    ))}
                  </div>
                </div>
              )}

              {/* Links */}
              {data.links.length > 0 && (
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">Related Sources</p>
                  <div className="space-y-1.5">
                    {data.links.map((l, i) => (
                      <a key={i} href={l.url} target="_blank" rel="noreferrer"
                        className="flex items-center gap-2 text-xs text-pharma-blue hover:underline">
                        <ExternalLink size={10} className="shrink-0"/>
                        <span className="truncate">{l.title}</span>
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-gray-400">Failed to load details.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function InsightCard({ insight, onClick }: { insight: Insight; onClick: () => void }) {

  const sourceName = insight.source_name || (insight.source_url
    ? new URL(insight.source_url).hostname.replace("www.", "")
    : null);
  const today = new Date().toISOString().slice(0, 10);
  const displayDate = insight.published_date && insight.published_date <= today
    ? insight.published_date
    : formatDateTime(insight.extracted_at);

  return (
    <div
      onClick={onClick}
      className="glass-panel rounded-xl p-5 cursor-pointer hover:bg-white/60 dark:hover:bg-slate-800/60 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-xs font-medium text-pharma-light">{insight.target_name}</span>
            <span className="text-gray-300 dark:text-gray-600">·</span>
            <span className="text-xs text-gray-400">{insight.category?.replace(/_/g, " ")}</span>
          </div>
          <p className="font-medium text-sm mb-1">{insight.topic}</p>
          <p className="text-sm text-gray-600 dark:text-[#64748b] line-clamp-2">{insight.what_they_said}</p>
        </div>
        <span className={cn("text-xs px-2 py-0.5 rounded-full shrink-0", SENTIMENT_COLORS[insight.sentiment] ?? SENTIMENT_COLORS.neutral)}>
          {insight.sentiment}
        </span>
      </div>
      <div className="flex items-center justify-between mt-2">
        <span className="text-xs text-gray-400">{displayDate}</span>
        {insight.source_url && (
          <a
            href={insight.source_url}
            target="_blank"
            rel="noreferrer"
            onClick={e => e.stopPropagation()}
            className="flex items-center gap-1 text-xs text-pharma-light hover:text-pharma-blue transition-colors"
            title={`Open source: ${sourceName}`}
          >
            <ExternalLink size={11} />
            {sourceName ? <span className="max-w-[160px] truncate">{sourceName}</span> : "Source"}
          </a>
        )}
      </div>
    </div>
  );
}

function SocialDetailModal({ point, onClose }: { point: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["social-detail", point],
    queryFn: () => api.socialDetail(point),
    staleTime: Infinity,
  });

  const PLATFORM_COLOR: Record<string, string> = {
    instagram: "text-pink-500 bg-pink-50 dark:bg-pink-900/20",
    twitter:   "text-sky-500 bg-sky-50 dark:bg-sky-900/20",
    linkedin:  "text-blue-600 bg-blue-50 dark:bg-blue-900/20",
    facebook:  "text-blue-500 bg-blue-50 dark:bg-blue-900/20",
  };
  const URGENCY_COLOR: Record<string, string> = {
    high: "bg-red-100 text-red-600 dark:bg-red-900/20 dark:text-red-400",
    medium: "bg-amber-100 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400",
    low: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose}/>
      <div className="relative w-full max-w-lg bg-white dark:bg-[#0d1424] border-l border-slate-200 dark:border-white/10 flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-slate-200/50 dark:border-white/10 flex-none">
          <div className="p-2 bg-orange-50 dark:bg-orange-900/20 rounded-lg shrink-0 mt-0.5">
            <TrendingUp size={14} className="text-orange-500"/>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-gray-900 dark:text-white leading-snug">{point}</p>
            {data && (
              <div className="flex items-center gap-2 mt-1">
                <span className={cn("text-[10px] font-bold px-1.5 py-0.5 rounded", URGENCY_COLOR[data.urgency] || URGENCY_COLOR.medium)}>
                  {data.urgency?.toUpperCase()} URGENCY
                </span>
                <span className="text-[10px] text-gray-400">♥ {data.total_likes} · 💬 {data.total_comments}</span>
              </div>
            )}
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 dark:hover:bg-[#1e3a5f]/40 rounded-lg text-gray-400 shrink-0">
            <X size={16}/>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {isLoading ? (
            <div className="space-y-3">
              {[85,60,75,50,90].map((w,i) => <div key={i} className="h-4 rounded bg-gray-100 dark:bg-[#1e3a5f]/40 animate-pulse" style={{width:`${w}%`}}/>)}
            </div>
          ) : data ? (
            <>
              {/* Platform breakdown */}
              {Object.keys(data.platform_stats).length > 0 && (
                <div className="flex gap-2 flex-wrap">
                  {Object.entries(data.platform_stats).map(([plt, stats]) => (
                    <div key={plt} className={cn("flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold", PLATFORM_COLOR[plt] || "text-gray-500 bg-gray-100")}>
                      <span className="uppercase">{plt}</span>
                      <span className="opacity-70">{stats.count} posts · ♥{stats.likes}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Hashtags */}
              {data.hashtags && data.hashtags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {data.hashtags.map((h, i) => (
                    <span key={i} className="text-[11px] px-2 py-0.5 bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 rounded-full border border-orange-200/50">
                      #{h.replace(/^#/, "")}
                    </span>
                  ))}
                </div>
              )}

              {/* Summary */}
              <div>
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">Trend Analysis</p>
                <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.summary}</p>
              </div>

              {/* So what */}
              {data.so_what && (
                <div className="bg-orange-50/60 dark:bg-orange-900/10 rounded-xl p-4 border border-orange-200/50 dark:border-orange-800/20">
                  <p className="text-[10px] font-black uppercase tracking-widest text-orange-600 mb-2">So what for Roche?</p>
                  <p className="text-sm text-gray-700 dark:text-[#cbd5e1] leading-relaxed">{data.so_what}</p>
                </div>
              )}

              {/* Action */}
              {data.action && (
                <div className="bg-amber-50 dark:bg-amber-900/10 rounded-xl p-4 border border-amber-200/50 dark:border-amber-800/20">
                  <p className="text-[10px] font-black uppercase tracking-widest text-amber-600 mb-2">Recommended Actions</p>
                  <p className="text-sm text-gray-700 dark:text-[#cbd5e1]">{data.action}</p>
                </div>
              )}

              {/* Posts */}
              {data.posts.length > 0 && (
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">Supporting Posts ({data.posts.length})</p>
                  <div className="space-y-2">
                    {data.posts.map((p, i) => (
                      <a key={i} href={p.url} target="_blank" rel="noreferrer"
                        className="flex items-start gap-2 glass-panel rounded-lg p-3 border border-slate-200/50 dark:border-white/5 hover:border-orange-300/50 transition-colors group">
                        <span className={cn("text-[9px] font-black uppercase px-1.5 py-0.5 rounded shrink-0 mt-0.5", (PLATFORM_COLOR[p.platform] || "text-gray-500 bg-gray-100").split(" ").slice(0,2).join(" "))}>{p.platform}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-gray-600 dark:text-[#94a3b8] line-clamp-3">{p.text}</p>
                          {p.topic && <span className="text-[10px] text-gray-400 mt-0.5 block">{p.topic}</span>}
                          {p.posted_at && <span className="text-[9px] text-gray-300 dark:text-gray-600">{p.posted_at.slice(0,10)}</span>}
                        </div>
                        <div className="text-right shrink-0 space-y-0.5">
                          <div className="text-[10px] text-gray-400">♥ {p.likes}</div>
                          <div className="text-[10px] text-gray-400">💬 {p.comments}</div>
                          {p.shares > 0 && <div className="text-[10px] text-gray-400">↗ {p.shares}</div>}
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-gray-400">Failed to load details.</p>
          )}
        </div>
      </div>
    </div>
  );
}
