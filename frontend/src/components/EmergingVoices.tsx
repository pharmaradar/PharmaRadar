import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, ExternalLink, UserPlus, Users } from "lucide-react";
import { api, EmergingVoice } from "@/lib/api";
import { cn } from "@/lib/utils";

const PERIODS = [
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
  { label: "180d", value: 180 },
];
const PLATFORMS = ["all", "twitter", "instagram", "linkedin", "facebook"];

function VoiceRow({ voice }: { voice: EmergingVoice }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const addAs = (type: "kol" | "competitor") =>
    navigate(`/targets?add=${encodeURIComponent(voice.author)}&type=${type}`);

  return (
    <div className="border border-slate-200/60 dark:border-white/10 rounded-lg overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <button onClick={() => setOpen(!open)} className="flex items-center gap-2 flex-1 min-w-0 text-left">
          {open ? <ChevronDown size={14} className="text-gray-400 shrink-0" /> : <ChevronRight size={14} className="text-gray-400 shrink-0" />}
          <span className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">{voice.author}</span>
          <span className="text-xs text-gray-400 shrink-0">
            {voice.posts} post{voice.posts > 1 ? "s" : ""} · {voice.engagement.toLocaleString()} eng · {voice.platforms.join(", ")}
          </span>
        </button>
        <div className="flex items-center gap-1.5 shrink-0">
          <button onClick={() => addAs("kol")}
            className="flex items-center gap-1 px-2 py-1 text-[11px] border border-blue-300 dark:border-blue-800 text-blue-600 dark:text-blue-400 rounded-md hover:bg-blue-50 dark:hover:bg-blue-900/20"
            title="Pre-fill the target form as a KOL">
            <UserPlus size={11} /> KOL
          </button>
          <button onClick={() => addAs("competitor")}
            className="flex items-center gap-1 px-2 py-1 text-[11px] border border-orange-300 dark:border-orange-800 text-orange-600 dark:text-orange-400 rounded-md hover:bg-orange-50 dark:hover:bg-orange-900/20"
            title="Pre-fill the target form as a competitor">
            <UserPlus size={11} /> Competitor
          </button>
        </div>
      </div>
      {open && (
        <div className="border-t border-slate-200/60 dark:border-white/10 px-3 py-2 space-y-2 bg-gray-50/50 dark:bg-white/[0.02]">
          {voice.examples.map((ex, i) => (
            <a key={i} href={ex.url} target="_blank" rel="noreferrer"
               className="block text-xs text-gray-600 dark:text-gray-300 hover:text-pharma-blue group">
              <span className="uppercase text-[10px] text-gray-400 mr-1.5">{ex.platform}</span>
              {ex.text || ex.url}
              <ExternalLink size={10} className="inline ml-1 text-gray-300 group-hover:text-pharma-blue" />
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

/** Emerging voices: authors active on our topics who are NOT tracked targets.
 *  Pure re-presentation of already-collected post data — no scraping, no LLM. */
export default function EmergingVoices({ query }: { query?: string }) {
  const [days, setDays] = useState(30);
  const [platform, setPlatform] = useState("all");

  const { data, isLoading } = useQuery({
    queryKey: ["emerging-voices", query || "", days, platform],
    queryFn: () => api.discovery.emergingVoices({ q: query, days, platform }),
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <Users size={15} className="text-pharma-blue dark:text-blue-300" />
          <h3 className="font-semibold text-sm text-gray-800 dark:text-gray-100">Emerging voices</h3>
          <span className="text-xs text-gray-400">
            {query ? `authors discussing “${query}”` : "most active non-tracked authors"} — not in your targets
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {PERIODS.map((p) => (
            <button key={p.value} onClick={() => setDays(p.value)}
              className={cn("px-2 py-1 text-[11px] rounded-md border",
                days === p.value
                  ? "bg-pharma-blue text-white border-pharma-blue"
                  : "border-slate-200 dark:border-[#1e3a5f] text-gray-500")}>
              {p.label}
            </button>
          ))}
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}
            className="text-[11px] border border-slate-200 dark:border-[#1e3a5f] rounded-md px-1.5 py-1 bg-transparent dark:bg-[#0f1e38] text-gray-500">
            {PLATFORMS.map((p) => <option key={p} value={p}>{p === "all" ? "All platforms" : p}</option>)}
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="text-sm text-gray-400 py-3">Scanning authors…</div>
      ) : !data || data.voices.length === 0 ? (
        <div className="text-sm text-gray-400 py-3">
          No non-tracked authors found{query ? ` for “${query}”` : ""} in this window.
        </div>
      ) : (
        <div className="space-y-2">
          {data.voices.map((v) => <VoiceRow key={v.author} voice={v} />)}
        </div>
      )}
    </div>
  );
}
