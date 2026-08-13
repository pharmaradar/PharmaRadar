import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays, Download, Eye, FileText, Globe, Search,
  RefreshCw, Users, X, Zap, ChevronDown, ChevronRight,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { useState, useMemo, useEffect } from "react";
import { cn } from "@/lib/utils";

type PdfFile = { path: string; name: string; size: number; url: string; uploadedAt?: string };

type Category = "all" | "summary" | "kol" | "burning" | "congress" | "global";

const CATEGORY_META: Record<Exclude<Category, "all">, { label: string; icon: React.ElementType; cls: string }> = {
  summary:  { label: "Combined Report", icon: FileText,     cls: "text-pharma-light" },
  kol:      { label: "Per-KOL Reports", icon: Users,        cls: "text-blue-500" },
  burning:  { label: "Burning Topics",  icon: Zap,          cls: "text-purple-500" },
  congress: { label: "Congress",        icon: CalendarDays, cls: "text-cyan-600" },
  global:   { label: "Global Synthesis", icon: Globe,       cls: "text-emerald-600" },
};

/** Newest first; the trailing two are historical and read-only. */
const SUMMARY_PREFIXES = [
  "Weekly_KOL_Report_",
  "Monthly_KOL_Report_",
  "Weekly_Report_",
  "Monthly_Report_",
  "Run_Summary_",
  "Daily_Summary_",
];

function categorize(p: PdfFile): Exclude<Category, "all"> {
  const path = p.path.toLowerCase();
  // Reports name themselves after the configured cadence ("Weekly_Report_…",
  // "Monthly_Report_…"). The earlier names are still recognised so PDFs already
  // in Blob storage keep categorising correctly rather than being stranded.
  if (SUMMARY_PREFIXES.some((prefix) => p.name.startsWith(prefix))) return "summary";
  if (path.includes("global")) return "global";
  if (path.includes("congress")) return "congress";
  if (path.includes("burning")) return "burning";
  return "kol";
}

function extractDate(p: PdfFile): string {
  const m = (p.path + " " + p.name).match(/\d{4}-\d{2}-\d{2}/);
  if (m) return m[0];
  if (p.uploadedAt) return p.uploadedAt.slice(0, 10);
  return "undated";
}

function prettyName(p: PdfFile, cat: Exclude<Category, "all">): string {
  if (cat === "kol") {
    // reports/{date}/{Target_Name}/{Target_Name}_{date}.pdf → "Target Name"
    const seg = p.path.split("/");
    const folder = seg.length >= 3 ? seg[seg.length - 2] : "";
    if (folder && !/^\d{4}-\d{2}-\d{2}$/.test(folder)) return folder.replace(/_/g, " ");
  }
  return p.name.replace(".pdf", "").replace(/_/g, " ");
}

// Public blob URLs work directly; local-dev /api/... paths need the auth header.
const objectUrlCache = new Map<string, string>();
async function resolvePdfUrl(url: string): Promise<string> {
  if (!url.startsWith("/api/")) return url;
  const hit = objectUrlCache.get(url);
  if (hit) return hit;
  const { useAuthStore } = await import("@/store/auth");
  const token = useAuthStore.getState().token;
  const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}`);
  const objectUrl = URL.createObjectURL(await res.blob());
  objectUrlCache.set(url, objectUrl);
  return objectUrl;
}

function PdfRow({ pdf, cat, onPreview }: {
  pdf: PdfFile; cat: Exclude<Category, "all">; onPreview: (url: string, name: string) => void;
}) {
  const meta = CATEGORY_META[cat];
  const Icon = meta.icon;
  const open = async (mode: "preview" | "download") => {
    try {
      const url = await resolvePdfUrl(pdf.url);
      if (mode === "preview") onPreview(url, pdf.name);
      else {
        const a = document.createElement("a");
        a.href = url; a.download = pdf.name; a.target = "_blank"; a.rel = "noreferrer";
        a.click();
      }
    } catch {
      alert("PDF not available");
    }
  };
  return (
    <div className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-[#1e2d4a] group">
      <div className="flex items-center gap-2.5 min-w-0">
        <Icon size={15} className={cn("shrink-0", meta.cls)} />
        <span className="text-sm text-gray-700 dark:text-[#e2e8f0] truncate">{prettyName(pdf, cat)}</span>
        <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-slate-500 shrink-0">
          {extractDate(pdf)}
        </span>
        <span className="text-xs text-gray-400 shrink-0">{(pdf.size / 1024).toFixed(0)} KB</span>
      </div>
      <div className="flex items-center gap-3 shrink-0 ml-3">
        <button onClick={() => open("preview")} className="text-pharma-light hover:text-pharma-blue" title="Preview">
          <Eye size={15} />
        </button>
        <button onClick={() => open("download")} className="text-pharma-light hover:text-pharma-blue" title="Download">
          <Download size={15} />
        </button>
      </div>
    </div>
  );
}

export default function Reports() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const { data: pdfs, isLoading, isError, error } = useQuery({ queryKey: ["pdfs"], queryFn: api.reports.list });
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewName, setPreviewName] = useState<string>("");
  const [genMsg, setGenMsg] = useState<string | null>(null);
  const [expandedSummaries, setExpandedSummaries] = useState<Set<string>>(new Set());
  const [summaryUrls, setSummaryUrls] = useState<Record<string, string>>({});

  // Two INDEPENDENT filters + a category tab
  const [category, setCategory] = useState<Category>("all");
  const [filterDate, setFilterDate] = useState("all");
  const [topicQuery, setTopicQuery] = useState("");

  const withMeta = useMemo(
    () => (pdfs ?? []).map(p => ({ pdf: p, cat: categorize(p), date: extractDate(p) })),
    [pdfs],
  );

  const dates = useMemo(() => {
    const d = new Set(withMeta.map(x => x.date).filter(x => x !== "undated"));
    return [...d].sort().reverse();
  }, [withMeta]);

  // Default the date filter to the newest date on first load (page stays scannable)
  useEffect(() => {
    if (dates.length && filterDate !== "all" && !dates.includes(filterDate)) setFilterDate(dates[0]);
    if (dates.length && filterDate === "") setFilterDate(dates[0]);
  }, [dates, filterDate]);

  const filtered = useMemo(() => withMeta.filter(({ pdf, cat, date }) => {
    if (category !== "all" && cat !== category) return false;
    if (filterDate !== "all" && date !== filterDate) return false;
    if (topicQuery.trim()) {
      const q = topicQuery.trim().toLowerCase();
      if (!(pdf.name.toLowerCase().includes(q) || pdf.path.toLowerCase().includes(q))) return false;
    }
    return true;
  }), [withMeta, category, filterDate, topicQuery]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: withMeta.length };
    for (const { cat } of withMeta) c[cat] = (c[cat] ?? 0) + 1;
    return c;
  }, [withMeta]);

  const summaries = filtered.filter(x => x.cat === "summary");
  const grouped = (Object.keys(CATEGORY_META) as Exclude<Category, "all">[])
    .filter(c => c !== "summary")
    .map(c => ({ cat: c, items: filtered.filter(x => x.cat === c) }))
    .filter(g => g.items.length > 0);

  const genPdfsMut = useMutation({
    mutationFn: api.runs.generatePdfs,
    onSuccess: () => {
      setGenMsg("PDF generation started — new files will appear as they finish (this can take a minute).");
      [5000, 12000, 20000, 30000, 45000].forEach(d =>
        setTimeout(() => qc.invalidateQueries({ queryKey: ["pdfs"] }), d));
      setTimeout(() => setGenMsg(null), 45000);
    },
    onError: (e) => {
      setGenMsg(`PDF generation failed: ${e instanceof Error ? e.message : "unknown error"}`);
    },
  });

  async function toggleSummary(pdf: PdfFile) {
    const next = new Set(expandedSummaries);
    if (next.has(pdf.path)) {
      next.delete(pdf.path);
    } else {
      next.add(pdf.path);
      if (!summaryUrls[pdf.path]) {
        try {
          const url = await resolvePdfUrl(pdf.url);
          setSummaryUrls(s => ({ ...s, [pdf.path]: url }));
        } catch { /* iframe will just fail */ }
      }
    }
    setExpandedSummaries(next);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0] mr-auto">KOL Report</h1>
        {isAdmin && (
          <button
            onClick={() => genPdfsMut.mutate()}
            disabled={genPdfsMut.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light disabled:opacity-50"
            title="Regenerate PDFs from existing insights without re-scraping"
          >
            <RefreshCw size={14} className={genPdfsMut.isPending ? "animate-spin" : ""} />
            {genPdfsMut.isPending ? "Generating…" : "Generate PDFs"}
          </button>
        )}
      </div>

      {/* ── Filters: category tabs + SEPARATE date + topic filters ── */}
      <div className="glass rounded-xl px-4 py-3 space-y-3">
        <div className="flex flex-wrap gap-1.5">
          <button onClick={() => setCategory("all")}
            className={cn("px-3 py-1.5 text-xs rounded-lg border transition-colors",
              category === "all" ? "bg-pharma-blue text-white border-pharma-blue" : "border-slate-200 dark:border-[#1e3a5f] text-slate-500 hover:text-slate-700 dark:hover:text-slate-200")}>
            All ({counts.all ?? 0})
          </button>
          {(Object.keys(CATEGORY_META) as Exclude<Category, "all">[]).map(c => {
            const Icon = CATEGORY_META[c].icon;
            return (
              <button key={c} onClick={() => setCategory(c)}
                className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors",
                  category === c ? "bg-pharma-blue text-white border-pharma-blue" : "border-slate-200 dark:border-[#1e3a5f] text-slate-500 hover:text-slate-700 dark:hover:text-slate-200")}>
                <Icon size={12} /> {CATEGORY_META[c].label} ({counts[c] ?? 0})
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <CalendarDays size={13} className="text-gray-400" />
            <span className="text-xs text-gray-400">Date</span>
            <select
              value={filterDate}
              onChange={e => setFilterDate(e.target.value)}
              className="text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg px-3 py-2 bg-white dark:bg-[#111827] text-gray-600 dark:text-[#94a3b8]"
            >
              <option value="all">All dates</option>
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 flex-1 min-w-[220px] max-w-sm">
            <Search size={13} className="text-gray-400 shrink-0" />
            <input
              value={topicQuery}
              onChange={e => setTopicQuery(e.target.value)}
              placeholder="Filter by topic, KOL or report name…"
              className="w-full text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg px-3 py-2 bg-white dark:bg-[#111827] text-gray-600 dark:text-[#e2e8f0]"
            />
            {topicQuery && (
              <button onClick={() => setTopicQuery("")} className="text-gray-400 hover:text-gray-600" title="Clear">
                <X size={13} />
              </button>
            )}
          </div>
          <span className="text-xs text-gray-400 ml-auto">{filtered.length} / {withMeta.length} reports</span>
        </div>
      </div>

      {genMsg && (
        <div className={genPdfsMut.isError
          ? "text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2"
          : "text-sm text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg px-4 py-2"}>
          {genMsg}
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : isError ? (
        <div className="text-center py-12 text-gray-400">
          <div>Failed to load PDFs.</div>
          <div className="mt-2 text-xs font-mono text-gray-500">
            {error instanceof Error ? error.message : "Unknown error"}
          </div>
        </div>
      ) : !pdfs?.length ? (
        <div className="text-center py-12 text-gray-400">No PDFs generated yet.</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No reports match these filters.</div>
      ) : (
        <>
          {/* Daily summaries — accordion with inline preview */}
          {summaries.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">Combined Report</h2>
              {summaries.map(({ pdf }) => {
                const expanded = expandedSummaries.has(pdf.path);
                return (
                  <div key={pdf.path} className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden">
                    <div
                      className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-[#1e2d4a]"
                      onClick={() => toggleSummary(pdf)}
                    >
                      <div className="flex items-center gap-2">
                        {expanded
                          ? <ChevronDown size={15} className="text-gray-400" />
                          : <ChevronRight size={15} className="text-gray-400" />}
                        <FileText size={15} className="text-pharma-light shrink-0" />
                        <span className="text-sm font-medium text-gray-700 dark:text-[#e2e8f0]">
                          {pdf.name.replace(".pdf", "").replace(/_/g, " ")}
                        </span>
                        <span className="text-xs text-gray-400 ml-2">{(pdf.size / 1024).toFixed(0)} KB</span>
                      </div>
                      <div className="flex items-center gap-3" onClick={e => e.stopPropagation()}>
                        <button
                          onClick={async () => {
                            try { setPreviewName(pdf.name); setPreviewUrl(await resolvePdfUrl(pdf.url)); }
                            catch { alert("PDF not available"); }
                          }}
                          className="text-pharma-light hover:text-pharma-blue"
                          title="Preview"
                        >
                          <Eye size={15} />
                        </button>
                        <a href={pdf.url.startsWith("/api/") ? undefined : pdf.url} download={pdf.name}
                          onClick={async (e) => {
                            if (pdf.url.startsWith("/api/")) {
                              e.preventDefault();
                              try {
                                const url = await resolvePdfUrl(pdf.url);
                                const a = document.createElement("a");
                                a.href = url; a.download = pdf.name; a.click();
                              } catch { alert("PDF not available"); }
                            }
                          }}
                          className="text-pharma-light hover:text-pharma-blue cursor-pointer" title="Download">
                          <Download size={15} />
                        </a>
                      </div>
                    </div>
                    {expanded && (
                      <div className="border-t border-gray-100 dark:border-[#1e3a5f]">
                        <iframe
                          src={summaryUrls[pdf.path] || (pdf.url.startsWith("/api/") ? "about:blank" : pdf.url)}
                          className="w-full border-0"
                          style={{ height: "70vh" }}
                          title={pdf.name}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Everything else, grouped by category */}
          {grouped.map(({ cat, items }) => (
            <div key={cat} className="space-y-2">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">
                {CATEGORY_META[cat].label} ({items.length})
              </h2>
              <div className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden divide-y divide-gray-50 dark:divide-[#1e3a5f]/50">
                {items.map(({ pdf }) => (
                  <PdfRow key={pdf.path} pdf={pdf} cat={cat}
                    onPreview={(url, name) => { setPreviewUrl(url); setPreviewName(name); }} />
                ))}
              </div>
            </div>
          ))}
        </>
      )}

      {previewUrl && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
          onClick={e => { if (e.target === e.currentTarget) setPreviewUrl(null); }}
        >
          <div className="overlay-panel rounded-xl w-full max-w-5xl h-[90vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-[#1e3a5f] shrink-0">
              <span className="font-medium text-sm text-gray-800 dark:text-[#e2e8f0] truncate">{previewName}</span>
              <button
                onClick={() => setPreviewUrl(null)}
                className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 ml-4"
                title="Close"
              >
                <X size={20} />
              </button>
            </div>
            <iframe
              src={previewUrl}
              className="flex-1 w-full border-0 rounded-b-xl"
              title={previewName}
            />
          </div>
        </div>
      )}
    </div>
  );
}
