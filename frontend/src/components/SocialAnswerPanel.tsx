import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, Loader2, Sparkles } from "lucide-react";
import { api, type SocialAnswer } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * A direct answer to the question in the search bar.
 *
 * Built because a real session went the other way: "what is the top 5 subject
 * that lung cancer patient want to discuss" returned 120 posts to read. The
 * material was on the screen; nothing read it.
 *
 * The caveat is as important as the answer. That question is about PATIENTS and
 * the posts matching it were overwhelmingly Roche and BMS corporate accounts —
 * so the panel leads with whose voices actually answered, and refuses to let a
 * confident-sounding paragraph stand in for evidence it does not have.
 */

function VoiceSplit({ voices }: { voices: Record<string, number> }) {
  const total = Object.values(voices).reduce((a, b) => a + b, 0);
  if (!total) return null;
  const colour: Record<string, string> = {
    patient: "bg-emerald-500", doctor: "bg-sky-500", kol: "bg-pharma-blue",
    organisation: "bg-orange-400", other: "bg-slate-300 dark:bg-slate-600",
  };
  return (
    <div className="space-y-1.5">
      <div className="flex h-2 rounded-full overflow-hidden bg-slate-100 dark:bg-[#1e3a5f]">
        {Object.entries(voices).map(([bucket, n]) => (
          <div key={bucket} title={`${bucket}: ${n}`}
               className={cn("h-full", colour[bucket] ?? colour.other)}
               style={{ width: `${(n / total) * 100}%` }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
        {Object.entries(voices).sort((a, b) => b[1] - a[1]).map(([bucket, n]) => (
          <span key={bucket} className="flex items-center gap-1">
            <span className={cn("w-2 h-2 rounded-full", colour[bucket] ?? colour.other)} />
            {bucket} {n}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function SocialAnswerPanel({ question }: { question: string }) {
  const ask = useMutation({
    mutationFn: () => api.answerSocialQuestion(question),
  });
  const data: SocialAnswer | undefined = ask.data;

  if (!question || question.trim().length < 5) return null;

  return (
    <div className="glass rounded-xl p-5 space-y-4 mb-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <Sparkles size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Answer this question</h2>
            <p className="text-xs text-gray-400">
              Reads the matched posts and answers directly, citing them — instead of
              leaving you to read the results
            </p>
          </div>
        </div>
        <button
          onClick={() => ask.mutate()}
          disabled={ask.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-pharma-blue text-white rounded-lg hover:bg-pharma-light disabled:opacity-50 transition-colors shrink-0">
          {ask.isPending ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
          {ask.isPending ? "Reading the posts…" : data ? "Ask again" : "Answer"}
        </button>
      </div>

      {ask.isError && (
        <p className="text-xs text-red-500">{(ask.error as Error)?.message}</p>
      )}

      {data && !data.answered && (
        <p className="text-sm text-gray-400">{data.reason}</p>
      )}

      {data?.answered && (
        <>
          {/* Whose voices answered — first, because it decides how to read the
              rest. An answer about patients built from corporate accounts is
              not a weaker answer, it is a different one. */}
          {data.evidence_note && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 dark:border-amber-900/40 bg-amber-50/60 dark:bg-amber-900/10 px-3 py-2.5">
              <AlertTriangle size={14} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <p className="text-xs text-amber-800 dark:text-amber-200">{data.evidence_note}</p>
            </div>
          )}

          {data.voices && <VoiceSplit voices={data.voices} />}

          {data.points.length > 0 && (
            <ol className="space-y-2">
              {data.points.map((point, i) => (
                <li key={i} className="flex gap-2.5">
                  <span className="w-5 h-5 rounded-full bg-pharma-blue/10 text-pharma-blue dark:text-blue-300 text-[11px] font-bold flex items-center justify-center shrink-0 mt-0.5">
                    {i + 1}
                  </span>
                  <span className="text-sm text-gray-700 dark:text-[#e2e8f0]">{point}</span>
                </li>
              ))}
            </ol>
          )}

          {data.answer_text && (
            <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{data.answer_text}</p>
          )}

          {data.so_what && (
            <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400 mb-1">
                So what
              </div>
              <p className="text-sm text-gray-700 dark:text-[#e2e8f0]">{data.so_what}</p>
            </div>
          )}

          {data.confidence && (
            <p className="text-xs text-gray-500 dark:text-[#94a3b8] italic">
              {data.confidence}
            </p>
          )}

          {data.citations.length > 0 && (
            <details>
              <summary className="text-xs font-semibold text-gray-500 cursor-pointer hover:text-pharma-blue">
                Sources ({data.citations.length})
              </summary>
              <ul className="mt-2 space-y-1.5">
                {data.citations.map((c) => (
                  <li key={c.n} className="text-xs text-gray-500 dark:text-[#94a3b8]">
                    <span className="text-pharma-blue dark:text-blue-300">[{c.n}]</span>{" "}
                    <span className="font-medium">{c.author || "unknown"}</span>
                    <span className="text-gray-400"> · {c.platform} · {c.voice}</span>
                    {c.url && (
                      <a href={c.url} target="_blank" rel="noreferrer"
                         className="ml-1 text-gray-400 hover:text-pharma-blue inline-block align-middle">
                        <ExternalLink size={10} />
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className="text-[11px] text-gray-400 pt-2 border-t border-slate-200/50 dark:border-white/5">
            Read {data.posts_used} of {data.posts_considered} matched posts
            {data.duplicates_removed ? ` · ${data.duplicates_removed} duplicates removed` : ""}.
          </p>
        </>
      )}
    </div>
  );
}
