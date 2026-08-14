import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, ExternalLink, Loader2, Send } from "lucide-react";
import { api, BurningTopicReport } from "@/lib/api";
import {
  Prose, SectionLabel, SubtopicList, VoiceChart, VolumeBlock,
} from "@/components/MarketSections";
import { cn } from "@/lib/utils";

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: "bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400",
    running: "bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400",
    done: "bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400",
    failed: "bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium capitalize",
      styles[status] || "bg-gray-100 text-gray-500")}>
      {(status === "pending" || status === "running") && <Loader2 size={11} className="animate-spin" />}
      {status}
    </span>
  );
}

function FollowupBox({ topicId, report }: { topicId: number; report: BurningTopicReport }) {
  const [thread, setThread] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [question, setQuestion] = useState("");

  const askMut = useMutation({
    mutationFn: (value: string) => api.burningTopics.followup(topicId, report.id, value, thread),
    onSuccess: (response, value) => {
      setThread((current) => [
        ...current,
        { role: "user", content: value },
        { role: "assistant", content: response.answer },
      ]);
      setQuestion("");
    },
  });

  const ask = () => {
    if (question.trim() && !askMut.isPending) askMut.mutate(question.trim());
  };

  return (
    <div className="mt-4 border-t border-slate-200/60 dark:border-white/10 pt-4">
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
        Ask a follow-up about this report
      </div>
      {thread.length > 0 && (
        <div className="space-y-2 mb-3 max-h-72 overflow-y-auto pr-1">
          {thread.map((message, index) => (
            <div key={index} className={cn(
              "text-sm rounded-lg px-3 py-2 whitespace-pre-wrap",
              message.role === "user"
                ? "bg-pharma-blue/10 text-slate-800 dark:text-slate-100 ml-8"
                : "bg-slate-100 dark:bg-white/5 text-slate-700 dark:text-slate-200 mr-8"
            )}>
              {message.content}
            </div>
          ))}
        </div>
      )}
      {askMut.isError && (
        <div className="text-xs text-red-500 mb-2">{(askMut.error as Error)?.message}</div>
      )}
      <div className="flex gap-2">
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") ask(); }}
          placeholder="e.g. Which post drove the most engagement, and why?"
          className="flex-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
          disabled={askMut.isPending}
        />
        <button
          onClick={ask}
          disabled={!question.trim() || askMut.isPending}
          className="px-3 py-2 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50 flex items-center gap-1.5"
        >
          {askMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Ask
        </button>
      </div>
    </div>
  );
}

interface ReportViewProps {
  scope: "topic" | "congress";
  scopeId: number;
  report: BurningTopicReport;
}

export default function ReportView({ scope, scopeId, report }: ReportViewProps) {
  const [downloading, setDownloading] = useState(false);

  const downloadPdf = async () => {
    if (report.pdf_url) {
      window.open(report.pdf_url, "_blank", "noopener,noreferrer");
      return;
    }
    setDownloading(true);
    try {
      const blob = scope === "topic"
        ? await api.burningTopics.downloadPdf(scopeId, report.id)
        : await api.congress.downloadPdf(scopeId, report.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${scope}_${scopeId}_report_${report.id}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      alert("PDF not available for this report");
    } finally {
      setDownloading(false);
    }
  };

  if (report.status === "pending" || report.status === "running") {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500 py-4">
        <Loader2 size={16} className="animate-spin" />
        Generating report - querying data, searching the web, synthesizing... (updates every 3s)
      </div>
    );
  }

  if (report.status === "failed") {
    return (
      <div className="text-sm text-red-500 py-2 whitespace-pre-wrap">
        {report.summary_md || "Report generation failed."}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-slate-400">
          Generated {new Date(report.created_at).toLocaleString()}
        </div>
        <button
          onClick={downloadPdf}
          disabled={downloading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-pharma-blue/40 text-pharma-blue dark:text-blue-300 rounded-lg hover:bg-pharma-blue/5 disabled:opacity-50"
        >
          {downloading ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          Download PDF
        </button>
      </div>

      {scope === "congress" && report.question_answers.length > 0 && (
        <div className="space-y-3">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Congress questions</div>
          {report.question_answers.map((item, index) => (
            <div key={item.question_id ?? index} className="rounded-lg border border-slate-200/60 dark:border-white/10 px-4 py-3">
              <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{item.question}</div>
              <p className="text-sm text-slate-600 dark:text-slate-300 whitespace-pre-wrap leading-relaxed mt-1.5">
                {item.answer || "No answer recorded."}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* The evidence base, stated up front.
          A report written from 8 documents reads very differently from one
          written from 50 — measured on this data, the thin one had the model
          writing "extreme scarcity" while the reader saw only confident prose.
          Showing the count lets the client weigh the conclusions themselves. */}
      {(report.item_count > 0 || report.window_days > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {report.item_count > 0 && (
            <span className={cn("px-2 py-0.5 rounded-full font-medium",
              report.item_count < 15
                ? "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300"
                : "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300")}>
              {report.item_count} sources analysed
            </span>
          )}
          {report.window_days > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-slate-600 dark:text-slate-300">
              last {report.window_days} days
            </span>
          )}
          {report.voice_exact_share > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-slate-600 dark:text-slate-300"
              title="Share of speakers identified from tracked records rather than inferred from the text">
              {report.voice_exact_share}% speakers identified
            </span>
          )}
          {report.item_count > 0 && report.item_count < 15 && (
            <span className="text-amber-600 dark:text-amber-400">
              Thin evidence — widen the period or add sources before acting on this.
            </span>
          )}
        </div>
      )}

      {report.summary_md && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
            {scope === "congress" ? "Main learnings" : "Summary"}
          </div>
          <p className="text-sm text-slate-700 dark:text-slate-200 whitespace-pre-wrap leading-relaxed">
            {report.summary_md}
          </p>
        </div>
      )}

      {report.key_findings.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Key findings</div>
          <ul className="space-y-1.5">
            {report.key_findings.map((finding, index) => (
              <li key={index} className="text-sm text-slate-700 dark:text-slate-200 flex gap-2">
                <span className="text-pharma-blue shrink-0 mt-0.5">-</span>{finding}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.so_what && (
        <div className="rounded-lg bg-blue-50/60 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 px-4 py-3">
          <div className="text-xs font-semibold text-blue-700 dark:text-blue-300 uppercase tracking-wider mb-1">So what</div>
          <p className="text-sm text-slate-700 dark:text-slate-200 whitespace-pre-wrap">{report.so_what}</p>
        </div>
      )}

      {/* Market-research sections, in the order the client listed them. Each is
          rendered only when present, so reports generated before these existed
          still display correctly. */}
      {report.what_is_said && (
        <div>
          <SectionLabel>What is being said</SectionLabel>
          <Prose text={report.what_is_said} />
        </div>
      )}

      {report.voice_rows?.length > 0 && (
        <div>
          <SectionLabel>Voice distribution</SectionLabel>
          <VoiceChart rows={report.voice_rows} exactShare={report.voice_exact_share} />
          {report.voices_note && <div className="mt-2"><Prose text={report.voices_note} /></div>}
        </div>
      )}

      {(report.volume?.total ?? 0) > 0 && (
        <div>
          <SectionLabel>Volume of mentions</SectionLabel>
          <VolumeBlock volume={report.volume} />
          {report.volume_note && <div className="mt-2"><Prose text={report.volume_note} /></div>}
        </div>
      )}

      {report.subtopics?.length > 0 && (
        <div>
          <SectionLabel>Key sub-topics to consider</SectionLabel>
          <SubtopicList items={report.subtopics} />
        </div>
      )}

      {report.important_posts.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
            {scope === "congress" ? "Posts and articles" : "Important posts"}
          </div>
          <div className="space-y-2">
            {report.important_posts.map((post, index) => (
              <a key={index} href={post.url} target="_blank" rel="noreferrer"
                 className="block rounded-lg border border-slate-200/60 dark:border-white/10 px-3 py-2 hover:border-pharma-blue/40 transition-colors group">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-pharma-blue">
                      {post.title || post.url}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {post.author || "?"} - {post.platform || "web"} - {post.engagement.toLocaleString()} engagement
                    </div>
                    {/* `says`/`benefit` split on newer reports; older ones only
                        carry the combined `why`. */}
                    {(post.says || post.why) && (
                      <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                        {post.says || post.why}
                      </div>
                    )}
                    {post.benefit && (
                      <div className="text-xs text-emerald-700 dark:text-emerald-300 mt-1">
                        <span className="text-[10px] font-semibold uppercase tracking-wider mr-1.5">
                          How we can use it
                        </span>
                        {post.benefit}
                      </div>
                    )}
                  </div>
                  <ExternalLink size={14} className="text-slate-300 group-hover:text-pharma-blue shrink-0 mt-1" />
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      {report.main_authors.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Main authors</div>
          <div className="flex flex-wrap gap-2">
            {report.main_authors.map((author, index) => (
              <div key={index} className="rounded-lg bg-slate-50 dark:bg-white/5 border border-slate-200/60 dark:border-white/10 px-3 py-1.5"
                   title={author.note || undefined}>
                <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{author.author}</span>
                <span className="text-xs text-slate-400 ml-2">
                  {author.posts} post{author.posts > 1 ? "s" : ""} - {author.engagement.toLocaleString()} eng
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {scope === "topic" && report.topic_id !== null && (
        <FollowupBox topicId={scopeId} report={report} />
      )}
    </div>
  );
}
