import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen, Building2, Download, ExternalLink,
  FileText, Layers, Loader2, RefreshCw, Users, X,
} from "lucide-react";
import { api, type SynthesisReport, type SynthesisScope } from "@/lib/api";
import { useGenQuota } from "@/hooks/useGenQuota";
import { cn } from "@/lib/utils";

/**
 * The three downloadable syntheses that sit at the very top of the dashboard.
 *
 * Each is generated on demand and then stays readable — the stored report is
 * shown without spending another LLM call, and polling only runs while one is
 * actually building.
 */

const SCOPES: {
  scope: SynthesisScope;
  label: string;
  blurb: string;
  icon: React.ElementType;
  accent: string;
}[] = [
  {
    scope: "kol",
    label: "KOL Synthesis",
    blurb: "What French KOLs said — last 30 days",
    icon: Users,
    accent: "text-blue-600 dark:text-blue-400",
  },
  {
    scope: "competitor",
    label: "Competitor Synthesis",
    blurb: "Competitor messaging in France — last 30 days",
    icon: Building2,
    accent: "text-purple-600 dark:text-purple-400",
  },
  {
    scope: "comprehensive",
    label: "Comprehensive Synthesis",
    blurb: "KOL and competitor intelligence combined",
    icon: Layers,
    accent: "text-emerald-600 dark:text-emerald-400",
  },
];

function Bullets({ items, label }: { items: string[]; label: string }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-2.5">{label}</p>
      <ul className="space-y-2.5">
        {items.map((text, i) => (
          <li key={i} className="flex items-start gap-2.5 text-[15px] leading-relaxed text-gray-700 dark:text-[#e2e8f0]">
            <span className="w-1.5 h-1.5 rounded-full bg-pharma-blue mt-2.5 shrink-0" />
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReportBody({ report }: { report: SynthesisReport }) {
  return (
    <div className="space-y-6">
      <Bullets items={report.main} label="Main information" />

      {report.so_what && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-2.5">So what</p>
          <p className="text-[15px] leading-relaxed text-gray-700 dark:text-[#e2e8f0] whitespace-pre-line">{report.so_what}</p>
        </div>
      )}

      <Bullets items={report.recommendations} label="Recommendations" />
      <Bullets items={report.watch} label="What to watch" />

      {report.key_posts?.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-2.5">
            Key articles &amp; posts
          </p>
          <div className="space-y-2">
            {report.key_posts.map((post, i) => (
              <div key={i} className="p-2.5 rounded-lg bg-gray-50/60 dark:bg-[#0d1424]/40 border border-slate-200/50 dark:border-white/5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-semibold text-pharma-blue dark:text-blue-300">{post.target}</span>
                  {post.source_name && <span className="text-[10px] text-gray-400">{post.source_name}</span>}
                  {post.date && <span className="text-[10px] text-gray-400">{post.date}</span>}
                  {post.url && (
                    <a href={post.url} target="_blank" rel="noreferrer"
                      className="text-gray-400 hover:text-pharma-blue" title="Open source">
                      <ExternalLink size={11} />
                    </a>
                  )}
                </div>
                <p className="text-[15px] leading-relaxed text-gray-700 dark:text-[#e2e8f0] mt-1">{post.why}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {report.sources?.length > 0 && (
        <details className="group">
          <summary className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 cursor-pointer hover:text-pharma-blue">
            Sources ({report.sources.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {report.sources.map((s) => (
              <li key={s.n} className="text-[13px] leading-relaxed text-gray-500 dark:text-[#94a3b8]">
                <span className="text-pharma-blue dark:text-blue-300">[{s.n}]</span> {s.target}
                {s.source_name && ` — ${s.source_name}`}
                {s.url && (
                  <a href={s.url} target="_blank" rel="noreferrer"
                    className="ml-1 text-gray-400 hover:text-pharma-blue inline-block align-middle">
                    <ExternalLink size={10} />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/**
 * The synthesis rendered in a dialog rather than inside its card.
 *
 * These reports run to six sections and a source list, so expanding one in place
 * pushed it down a third of the dashboard grid and left it in a ~380px column —
 * a reading width of roughly forty characters, with the other two cards stranded
 * beside a very tall neighbour. A dialog gives it the full width and its own
 * scroll, and leaves the grid alone.
 */
function ReportModal({ report, label, blurb, icon: Icon, accent, onClose, onPdf }: {
  report: SynthesisReport;
  label: string;
  blurb: string;
  icon: React.ElementType;
  accent: string;
  onClose: () => void;
  onPdf: () => void;
}) {
  // Escape closes, and the page behind must not scroll while the dialog owns
  // the scroll — otherwise the wheel moves the dashboard under the overlay.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={onClose} role="dialog" aria-modal="true" aria-label={label}>
      <div className="bg-white dark:bg-[#111827] rounded-2xl shadow-2xl w-full max-w-3xl max-h-[88vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>

        {/* Header stays put so the title and PDF button survive a long scroll. */}
        <div className="flex-none flex items-start justify-between gap-3 px-6 py-4 border-b border-slate-200/60 dark:border-white/10">
          <div className="flex items-start gap-3 min-w-0">
            <div className="p-2 bg-slate-100 dark:bg-white/5 rounded-lg shrink-0">
              <Icon size={18} className={accent} />
            </div>
            <div className="min-w-0">
              <h2 className="font-bold text-base text-gray-900 dark:text-white">{label}</h2>
              <p className="text-xs text-gray-400 leading-snug">{blurb}</p>
              <p className="text-[11px] text-gray-400 mt-1">
                {report.insight_count} statements · {new Date(report.generated_at).toLocaleDateString()}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {report.pdf_url && (
              <button onClick={onPdf}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-pharma-blue text-white rounded-lg hover:bg-pharma-light transition-colors">
                <Download size={12} /> PDF
              </button>
            )}
            <button onClick={onClose} aria-label="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/5 transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <ReportBody report={report} />
        </div>
      </div>
    </div>
  );
}


function SynthesisCard({ scope, label, blurb, icon: Icon, accent }: (typeof SCOPES)[number]) {
  const qc = useQueryClient();
  const { can, spent } = useGenQuota();
  const [open, setOpen] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["synthesis", scope],
    queryFn: () => api.reports.synthesis(scope),
    // Only poll while this report is actually building.
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });

  const running = data?.status === "running";
  const report = data?.result ?? null;

  const genMut = useMutation({
    mutationFn: () => api.reports.triggerSynthesis(scope),
    onSuccess: () => {
      spent();
      qc.invalidateQueries({ queryKey: ["synthesis", scope] });
    },
  });

  const openPdf = async () => {
    if (!report?.pdf_url) return;
    setPdfError(null);
    try {
      await api.reports.openPdf(report.pdf_url);
    } catch {
      setPdfError("PDF not available");
    }
  };

  return (
    <div className="glass rounded-xl p-4 flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-slate-100 dark:bg-white/5 rounded-lg shrink-0">
            <Icon size={16} className={accent} />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-sm truncate">{label}</h3>
            <p className="text-xs text-gray-400 leading-snug">{blurb}</p>
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {report?.pdf_url && (
          <button onClick={openPdf}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-pharma-blue text-white rounded-lg hover:bg-pharma-light transition-colors">
            <Download size={12} /> PDF
          </button>
        )}
        {(can(`synthesis_${scope}`) || !report) && (
          <button onClick={() => genMut.mutate()} disabled={running || genMut.isPending}
            title={report ? "Regenerate from the latest data (1 per day)" : "Generate this synthesis"}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 disabled:opacity-50 transition-colors">
            {running || genMut.isPending
              ? <Loader2 size={12} className="animate-spin" />
              : <RefreshCw size={12} />}
            {running ? "Generating…" : report ? "Regenerate" : "Generate"}
          </button>
        )}
        {report && (
          <button onClick={() => setOpen(true)}
            title="Read the full synthesis"
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg text-gray-600 dark:text-[#94a3b8] hover:bg-slate-50 dark:hover:bg-white/5 hover:text-pharma-blue transition-colors">
            <BookOpen size={12} /> Read
          </button>
        )}
      </div>

      <div className="mt-2 text-[11px] text-gray-400 min-h-[16px]">
        {running && "Building the report — this takes a minute."}
        {!running && report?.error && <span className="text-amber-500">{report.error}</span>}
        {!running && data?.status === "error" && data.error && (
          <span className="text-red-400">{data.error}</span>
        )}
        {!running && report && !report.error && (
          <>
            {report.insight_count} statements ·{" "}
            {new Date(report.generated_at).toLocaleDateString()}
            {!report.pdf_url && " · PDF unavailable"}
          </>
        )}
        {!running && !report && data?.status !== "error" && (
          <span className="flex items-center gap-1">
            <FileText size={11} /> Not generated yet
          </span>
        )}
        {pdfError && <span className="text-red-400"> · {pdfError}</span>}
      </div>

      {open && report && (
        <ReportModal
          report={report}
          label={label}
          blurb={blurb}
          icon={Icon}
          accent={accent}
          onClose={() => setOpen(false)}
          onPdf={openPdf}
        />
      )}
    </div>
  );
}

export default function SynthesisExports() {
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-[#e2e8f0]">Downloadable syntheses</h2>
        <span className="text-[11px] text-gray-400">Last 30 days</span>
      </div>
      <div className={cn("grid gap-4", "grid-cols-1 lg:grid-cols-3")}>
        {SCOPES.map((s) => <SynthesisCard key={s.scope} {...s} />)}
      </div>
    </div>
  );
}
