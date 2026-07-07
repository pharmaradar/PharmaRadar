import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Eye, FileText, X, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { useState, useMemo, useEffect } from "react";

type PdfFile = { path: string; name: string; size: number; url: string; uploadedAt?: string };

export default function Reports() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const { data: pdfs, isLoading, isError, error } = useQuery({ queryKey: ["pdfs"], queryFn: api.reports.list });
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewName, setPreviewName] = useState<string>("");
  const [genMsg, setGenMsg] = useState<string | null>(null);
  const [expandedSummaries, setExpandedSummaries] = useState<Set<string>>(new Set());
  const [filterDate, setFilterDate] = useState("");

  const dates = useMemo(() => {
    const d = new Set((pdfs ?? []).map(p => p.path.split("/")[1]).filter(Boolean));
    return [...d].sort().reverse();
  }, [pdfs]);

  // Default to the most recent date so the page shows one day at a time (never all days at once)
  useEffect(() => {
    if (dates.length && !dates.includes(filterDate)) setFilterDate(dates[0]);
  }, [dates, filterDate]);

  const genPdfsMut = useMutation({
    mutationFn: api.runs.generatePdfs,
    onSuccess: () => {
      setGenMsg("PDF generation started — new files will appear as they finish (this can take a minute).");
      // Clear the date so the default effect jumps to the newest date once it's generated.
      setFilterDate("");
      // Poll the list several times since generating all target PDFs takes longer than one refresh.
      [5000, 12000, 20000, 30000, 45000].forEach(d =>
        setTimeout(() => qc.invalidateQueries({ queryKey: ["pdfs"] }), d));
      setTimeout(() => setGenMsg(null), 45000);
    },
    onError: (e) => {
      setGenMsg(`PDF generation failed: ${e instanceof Error ? e.message : "unknown error"}`);
    },
  });

  const summaries: PdfFile[] = useMemo(() =>
    (pdfs ?? []).filter(p => p.name.startsWith("Daily_Summary_") && (!filterDate || p.path.includes(filterDate))),
    [pdfs, filterDate]);
  const targets: PdfFile[] = useMemo(() =>
    (pdfs ?? []).filter(p => !p.name.startsWith("Daily_Summary_") && (!filterDate || p.path.includes(filterDate))),
    [pdfs, filterDate]);

  function toggleSummary(path: string) {
    setExpandedSummaries(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0] mr-auto">Reports</h1>
        {dates.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">Date</span>
            <select
              value={filterDate}
              onChange={e => setFilterDate(e.target.value)}
              className="text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg px-3 py-2 bg-white dark:bg-[#111827] text-gray-600 dark:text-[#94a3b8]"
            >
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        )}
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
      ) : (
        <>
          {summaries.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">Daily Summaries</h2>
              {summaries.map(pdf => {
                const expanded = expandedSummaries.has(pdf.path);
                return (
                  <div key={pdf.path} className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden">
                    <div
                      className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-[#1e2d4a]"
                      onClick={() => toggleSummary(pdf.path)}
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
                          onClick={() => { setPreviewUrl(pdf.url); setPreviewName(pdf.name); }}
                          className="text-pharma-light hover:text-pharma-blue"
                          title="Preview"
                        >
                          <Eye size={15} />
                        </button>
                        <a href={pdf.url} download={pdf.name} className="text-pharma-light hover:text-pharma-blue" title="Download">
                          <Download size={15} />
                        </a>
                      </div>
                    </div>
                    {expanded && (
                      <div className="border-t border-gray-100 dark:border-[#1e3a5f]">
                        <iframe
                          src={pdf.url}
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

          {targets.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">Individual Reports</h2>
              <div className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden">
                <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[500px]">
                  <thead>
                    <tr className="border-b border-gray-100 dark:border-[#1e3a5f] text-left text-xs text-gray-500 uppercase tracking-wider">
                      <th className="px-4 py-3">File</th>
                      <th className="px-4 py-3">Size</th>
                      <th className="px-4 py-3 w-20"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-[#1e3a5f]/50">
                    {targets.map(pdf => (
                      <tr key={pdf.path} className="hover:bg-gray-50 dark:hover:bg-[#1e2d4a]">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <FileText size={15} className="text-pharma-light shrink-0" />
                            <span className="font-mono text-xs text-gray-600 dark:text-[#64748b]">{pdf.path}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-gray-500 text-xs">{(pdf.size / 1024).toFixed(0)} KB</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => { setPreviewUrl(pdf.url); setPreviewName(pdf.name); }}
                              className="text-pharma-light hover:text-pharma-blue"
                              title="Preview PDF"
                            >
                              <Eye size={15} />
                            </button>
                            <a href={pdf.url} download={pdf.name} className="text-pharma-light hover:text-pharma-blue" title="Download">
                              <Download size={15} />
                            </a>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {previewUrl && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
          onClick={e => { if (e.target === e.currentTarget) setPreviewUrl(null); }}
        >
          <div className="glass-panel rounded-xl shadow-2xl w-full max-w-5xl h-[90vh] flex flex-col">
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
