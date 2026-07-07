import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Flame, ArrowRight, X, Heart, MessageCircle, Eye, ExternalLink } from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { api, type SocialPost } from "@/lib/api";
import { DescribeModal } from "./SocialTrends";

const WAVE = ["#f97316", "#0ea5e9", "#14b8a6", "#a855f7", "#ef4444", "#f59e0b"];

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function SocialTrendsSummary() {
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);

  const { data: ts } = useQuery({
    queryKey: ["social-timeseries"],
    queryFn: () => api.social.timeseries(180, 6),
    refetchInterval: 60_000,
  });

  const topics = ts?.topics ?? [];
  const series = ts?.series ?? [];
  const hasData = topics.length > 0 && series.length > 0;

  return (
    <div className="glass rounded-xl">
      <div className="flex items-center justify-between px-6 pt-6 pb-4 border-b border-slate-200/50 dark:border-slate-800/50">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-orange-50 dark:bg-orange-900/20 rounded-lg">
            <Flame size={18} className="text-orange-500 shrink-0" />
          </div>
          <div>
            <h2 className="font-semibold text-sm">Pharma Market Trends</h2>
            <p className="text-[11px] text-gray-400">Top topics trending across social — engagement over time</p>
          </div>
        </div>
        <Link to="/social"
          className="flex items-center gap-1 text-xs font-medium text-pharma-light hover:text-pharma-blue transition-colors">
          View all <ArrowRight size={13} />
        </Link>
      </div>

      {!hasData ? (
        <div className="text-center py-10 text-gray-400 text-sm">
          No social trends yet — run a scan from the <Link to="/social" className="text-pharma-light underline">Social Trends</Link> page.
        </div>
      ) : (
        <div className="p-5">
          {/* Wave chart */}
          <div onMouseDown={e => e.preventDefault()}>
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={series} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
                <defs>
                  {topics.map((t, i) => (
                    <linearGradient key={t} id={`wave-${i}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={WAVE[i % WAVE.length]} stopOpacity={0.5} />
                      <stop offset="95%" stopColor={WAVE[i % WAVE.length]} stopOpacity={0.05} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 10, fill: "#94a3b8" }}
                  axisLine={false} tickLine={false} minTickGap={24} />
                <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} axisLine={false} tickLine={false}
                  tickFormatter={(v) => fmt(v as number)} width={36} />
                <Tooltip
                  contentStyle={{ backgroundColor: "rgba(15,23,42,0.92)", borderRadius: "8px", border: "none", color: "#f1f5f9", fontSize: 12 }}
                  labelFormatter={(l) => `Week of ${shortDate(l as string)}`}
                  formatter={(v, n) => [`${fmt(v as number)} engagement`, n as string]} />
                {topics.map((t, i) => (
                  <Area key={t} type="monotone" dataKey={t} stackId="1"
                    stroke={WAVE[i % WAVE.length]} fill={`url(#wave-${i})`} strokeWidth={2}
                    style={{ cursor: "pointer" }}
                    activeDot={{ r: 4, onClick: () => setSelectedTopic(t) }}
                    onClick={() => setSelectedTopic(t)} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Clickable topic legend — reliable way to open the detail sidebar */}
          <div className="flex flex-wrap gap-2 mt-3">
            {topics.map((t, i) => (
              <button key={t} onClick={() => setSelectedTopic(t)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border border-gray-200 dark:border-[#1e3a5f] hover:border-pharma-light transition-colors">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: WAVE[i % WAVE.length] }} />
                <span className="text-gray-600 dark:text-[#94a3b8]">{t}</span>
              </button>
            ))}
          </div>
          <p className="text-[11px] text-gray-400 mt-2">Click a topic to see the posts behind the trend.</p>
        </div>
      )}

      {selectedTopic && <TopicSidebar topic={selectedTopic} onClose={() => setSelectedTopic(null)} />}
    </div>
  );
}

function TopicSidebar({ topic, onClose }: { topic: string; onClose: () => void }) {
  const [describe, setDescribe] = useState<SocialPost | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["social-topic-posts", topic],
    queryFn: () => api.social.discover(topic, false),
  });
  const posts = data?.results ?? [];

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-md bg-white dark:bg-slate-900 shadow-2xl flex flex-col h-full">
        <div className="flex items-start justify-between p-5 border-b border-gray-100 dark:border-slate-800 shrink-0">
          <div>
            <p className="text-xs text-gray-400 mb-0.5">Trending topic</p>
            <h2 className="font-bold text-gray-900 dark:text-[#e2e8f0] text-base">{topic}</h2>
            <p className="text-xs text-gray-400 mt-0.5">{posts.length} post{posts.length !== 1 ? "s" : ""}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-slate-800"><X size={18} /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {isLoading ? (
            <div className="text-center py-12 text-gray-400 text-sm">Loading…</div>
          ) : posts.length === 0 ? (
            <div className="text-center py-12 text-gray-400 text-sm">No posts for this topic.</div>
          ) : posts.map(p => (
            <div key={p.id} onClick={() => setDescribe(p)}
              className="bg-gray-50 dark:bg-slate-800/50 rounded-xl p-3 border border-gray-100 dark:border-slate-700/50 cursor-pointer hover:border-pharma-light/40 transition-all">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] px-1.5 py-0.5 rounded font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/20 dark:text-orange-300 capitalize">{p.platform}</span>
                {p.author && <span className="text-xs font-semibold text-pharma-light truncate">{p.author}</span>}
                {p.posted_at && <span className="text-[11px] text-gray-400 ml-auto">{shortDate(p.posted_at)}</span>}
              </div>
              <p className="text-sm text-gray-700 dark:text-[#94a3b8] line-clamp-2">{p.text || "—"}</p>
              <div className="flex items-center gap-3 mt-2 text-[11px] text-gray-500">
                {p.likes > 0 && <span className="flex items-center gap-1"><Heart size={11} />{fmt(p.likes)}</span>}
                {p.comments > 0 && <span className="flex items-center gap-1"><MessageCircle size={11} />{fmt(p.comments)}</span>}
                {p.views > 0 && <span className="flex items-center gap-1"><Eye size={11} />{fmt(p.views)}</span>}
                <a href={p.post_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                  className="ml-auto text-pharma-light hover:text-pharma-blue"><ExternalLink size={12} /></a>
              </div>
            </div>
          ))}
        </div>
      </div>

      {describe && <DescribeModal post={describe} onClose={() => setDescribe(null)} />}
    </div>
  );
}
