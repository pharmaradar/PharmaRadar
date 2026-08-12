import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3, Download, ExternalLink, FileSearch,
  Loader2, Sparkles, Users,
} from "lucide-react";
import { api, type MarketReport } from "@/lib/api";
import { useGenQuota } from "@/hooks/useGenQuota";
import {
  Prose, SubtopicList, VoiceChart, VolumeBlock,
} from "@/components/MarketSections";

/**
 * The market-research report for an ad-hoc question.
 *
 * Voice distribution and volume are computed server-side from the underlying
 * rows, so they are rendered as figures with their caveats rather than as prose
 * the model produced. The caveats are deliberately visible: a chart that
 * presents inference as fact is worse than no chart, because it gets acted on.
 */

function Section({ title, index, children }: {
  title: string; index: number; children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-[#e2e8f0]">
        <span className="w-5 h-5 rounded-full bg-pharma-blue/10 text-pharma-blue dark:text-blue-300 text-[11px] flex items-center justify-center font-bold">
          {index}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}





function ReportBody({ report }: { report: MarketReport }) {
  return (
    <div className="space-y-6">
      <Section title="Executive summary" index={1}><Prose text={report.exec_summary} /></Section>

      <Section title="So what — strategic implications" index={2}>
        <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
          <Prose text={report.so_what} />
        </div>
      </Section>

      <Section title="What is being said" index={3}><Prose text={report.what_is_said} /></Section>

      <Section title="Voice distribution" index={4}>
        <VoiceChart rows={report.voice_rows} exactShare={report.voice_exact_share} />
        <Prose text={report.voices_note} />
      </Section>

      <Section title="Volume of mentions" index={5}>
        <VolumeBlock volume={report.volume} />
        <Prose text={report.volume_note} />
      </Section>

      <Section title="Key sub-topics to consider" index={6}>
        <SubtopicList items={report.subtopics} />
      </Section>

      {report.key_posts?.length > 0 && (
        <Section title="Key articles & posts" index={7}>
          <div className="space-y-2">
            {report.key_posts.map((post, i) => (
              <div key={i} className="p-2.5 rounded-lg bg-gray-50/60 dark:bg-[#0d1424]/40 border border-slate-200/50 dark:border-white/5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-semibold text-pharma-blue dark:text-blue-300">
                    {post.author || post.source_name}
                  </span>
                  <span className="text-[10px] text-gray-400">{post.kind}</span>
                  {post.date && <span className="text-[10px] text-gray-400">{post.date}</span>}
                  {post.url && (
                    <a href={post.url} target="_blank" rel="noreferrer"
                      className="text-gray-400 hover:text-pharma-blue" title="Open source">
                      <ExternalLink size={11} />
                    </a>
                  )}
                </div>
                <p className="text-sm text-gray-700 dark:text-[#e2e8f0] mt-1">{post.why}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {report.sources?.length > 0 && (
        <details>
          <summary className="text-xs font-semibold text-gray-500 cursor-pointer hover:text-pharma-blue">
            Sources ({report.sources.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {report.sources.map((source) => (
              <li key={source.n} className="text-xs text-gray-500 dark:text-[#94a3b8]">
                <span className="text-pharma-blue dark:text-blue-300">[{source.n}]</span>{" "}
                {source.author || source.source_name} — {source.kind}
                {source.url && (
                  <a href={source.url} target="_blank" rel="noreferrer"
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

export default function MarketResearchReport({ question, windowDays = 30, lang = "fr" }: {
  question: string; windowDays?: number; lang?: string;
}) {
  const qc = useQueryClient();
  const { can, spent } = useGenQuota();
  const [reportId, setReportId] = useState<number | null>(null);

  const { data: report } = useQuery({
    queryKey: ["market-report", reportId],
    queryFn: () => api.discovery.report(reportId as number),
    enabled: reportId != null,
    // Poll only while it is actually building.
    refetchInterval: (q) =>
      q.state.data && ["pending", "running"].includes(q.state.data.status) ? 3000 : false,
  });

  const genMut = useMutation({
    mutationFn: () => api.discovery.createReport(question, windowDays, lang),
    onSuccess: (res) => {
      setReportId(res.id);
      spent();
      qc.invalidateQueries({ queryKey: ["market-reports"] });
    },
  });

  const building = genMut.isPending
    || (report != null && ["pending", "running"].includes(report.status));

  if (!question) return null;

  return (
    <div className="glass rounded-xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <FileSearch size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Market research report</h2>
            <p className="text-xs text-gray-400">
              Executive summary · So what · What is being said · Voices · Volume · Sub-topics
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {report?.pdf_url && (
            <button
              onClick={() => api.reports.openPdf(report.pdf_url!).catch(() => undefined)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-pharma-blue text-white rounded-lg hover:bg-pharma-light transition-colors">
              <Download size={12} /> PDF
            </button>
          )}
          {(can("market_report") || !report) && (
            <button onClick={() => genMut.mutate()} disabled={building}
              title="Generate a structured report for this question (1 per day)"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 disabled:opacity-50 transition-colors">
              {building ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              {building ? "Researching…" : report ? "Regenerate" : "Generate report"}
            </button>
          )}
        </div>
      </div>

      {building && (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />
          Gathering KOL statements, social posts and articles, then writing the report — about a minute.
        </p>
      )}

      {genMut.isError && (
        <p className="text-xs text-red-400">{(genMut.error as Error)?.message}</p>
      )}

      {report?.status === "failed" && (
        <p className="text-xs text-red-400">Generation failed: {report.error || "unknown error"}</p>
      )}

      {report?.status === "done" && report.error && (
        <p className="text-xs text-amber-500">{report.error}</p>
      )}

      {report?.status === "done" && !report.error && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-gray-400 border-b border-slate-200/50 dark:border-white/5 pb-3">
            <span className="flex items-center gap-1"><BarChart3 size={11} />{report.item_count} items analysed</span>
            <span className="flex items-center gap-1"><Users size={11} />{report.voice_exact_share}% voices identified</span>
            <span>last {report.window_days} days</span>
          </div>
          <ReportBody report={report} />
        </>
      )}

      {!report && !building && (
        <p className="text-sm text-gray-400">
          Ask a question above, then generate a structured report answering it from the
          KOL, social and web material already collected.
        </p>
      )}
    </div>
  );
}
