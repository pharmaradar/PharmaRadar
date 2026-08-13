import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ExternalLink, Loader2, Sparkles, X,
} from "lucide-react";
import { api, type PostAnalysis } from "@/lib/api";
import { Prose, SubtopicList } from "@/components/MarketSections";

/**
 * One post, analysed on its own, in the report format used everywhere else.
 *
 * Two sections are deliberately NOT the ones a whole-corpus report carries:
 *
 * - "Voice distribution" is a classification here, not a distribution. One post
 *   has one author, so a percentage chart would be theatre.
 * - "Volume of mentions" is always 1 for a single post, so the useful question
 *   is how far it travelled compared with other posts on its platform. X and
 *   LinkedIn arrive through search with no engagement data at all, so that is
 *   stated rather than shown as a zero.
 */

const BUCKET_LABEL: Record<string, string> = {
  kol: "KOL",
  doctor: "Doctor / HCP",
  patient: "Patient or caregiver",
  organisation: "Organisation (industry, institution, press)",
  other: "Unclassified",
};

function Section({ n, title, children }: {
  n: number; title: string; children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-[#e2e8f0]">
        <span className="w-5 h-5 rounded-full bg-pharma-blue/10 text-pharma-blue dark:text-blue-300 text-[11px] flex items-center justify-center font-bold">
          {n}
        </span>
        {title}
      </h4>
      {children}
    </section>
  );
}

function ReachBlock({ reach }: { reach: PostAnalysis["reach"] }) {
  if (!reach) return null;
  if (!reach.available) {
    return (
      <p className="flex items-start gap-1.5 text-xs text-gray-500 dark:text-[#94a3b8] p-3 rounded-lg bg-slate-50/60 dark:bg-white/5">
        <AlertTriangle size={13} className="shrink-0 mt-0.5 text-amber-500" />
        {reach.note ?? "Engagement is not available for this platform."}
      </p>
    );
  }
  const cells = [
    { label: "Likes", value: reach.likes },
    { label: "Comments", value: reach.comments },
    { label: "Views", value: reach.views },
    { label: "Total", value: reach.engagement },
  ].filter((c) => c.value > 0 || c.label === "Total");

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {cells.map((c) => (
          <div key={c.label} className="p-2 rounded-lg bg-slate-50/60 dark:bg-white/5">
            <p className="text-base font-bold text-gray-800 dark:text-white tabular-nums">{c.value}</p>
            <p className="text-[10px] text-gray-400">{c.label}</p>
          </div>
        ))}
      </div>
      {reach.vs_average != null && (
        <p className="text-xs text-gray-500 dark:text-[#94a3b8]">
          <span className={reach.vs_average >= 1
            ? "text-emerald-600 dark:text-emerald-400 font-semibold"
            : "text-amber-600 dark:text-amber-400 font-semibold"}>
            {reach.vs_average}×
          </span>{" "}
          the average {reach.platform} post in this corpus ({reach.platform_average}).
        </p>
      )}
    </div>
  );
}

export default function PostAnalysisPanel({ postId, post, onClose, withDescribe = false }: {
  postId: number;
  post?: { text?: string | null; author?: string | null; platform?: string; url?: string | null };
  onClose: () => void;
  /** Also show the older what / so-what-for-pharma describe, which Social
   *  Trends already offered and which the client wants kept alongside this. */
  withDescribe?: boolean;
}) {
  const qc = useQueryClient();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  // Only read the cache on open — analysing costs an LLM call, so it is an
  // explicit action rather than a side effect of clicking a post.
  const { data: cached } = useQuery({
    queryKey: ["post-analysis", postId],
    queryFn: () => api.social.postAnalysis(postId),
    retry: false,
  });

  const { data: describe } = useQuery({
    queryKey: ["social-describe", postId],
    queryFn: () => api.social.describe(postId),
    enabled: withDescribe,
    staleTime: Infinity,
  });

  const analyseMut = useMutation({
    mutationFn: (refresh: boolean) => api.social.analysePost(postId, refresh),
    onSuccess: (res) => qc.setQueryData(["post-analysis", postId], res),
  });

  const sections = analyseMut.data?.sections ?? cached?.sections ?? null;
  const busy = analyseMut.isPending;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-2xl h-full overflow-y-auto overlay-panel border-l p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-bold text-pharma-blue dark:text-[#e2e8f0]">
              Post analysis
            </h2>
            <p className="text-xs text-gray-400 truncate">
              {post?.author && <span>{post.author} · </span>}
              <span className="capitalize">{post?.platform}</span>
              {post?.url && (
                <a href={post.url} target="_blank" rel="noreferrer"
                  className="ml-2 inline-flex items-center gap-1 text-pharma-blue hover:underline">
                  <ExternalLink size={11} /> Open post
                </a>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button onClick={() => analyseMut.mutate(!!sections)} disabled={busy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs bg-pharma-blue text-white rounded-lg hover:bg-pharma-light disabled:opacity-50 transition-colors">
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              {busy ? "Analysing…" : sections ? "Regenerate" : "Analyse"}
            </button>
            <button onClick={onClose} aria-label="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:bg-slate-100 dark:hover:bg-white/5">
              <X size={16} />
            </button>
          </div>
        </div>

        {post?.text && (
          <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed p-3 rounded-lg bg-slate-50/60 dark:bg-white/5">
            {post.text}
          </p>
        )}

        {withDescribe && describe?.description && (
          <div className="rounded-lg border border-slate-200/60 dark:border-white/10 p-3 space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              What this is
            </p>
            <Prose text={describe.description} />
            {describe.so_what && (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">
                  So what for pharma
                </p>
                <Prose text={describe.so_what} />
              </>
            )}
          </div>
        )}

        {analyseMut.isError && (
          <p className="text-xs text-red-400">
            {(analyseMut.error as Error)?.message?.slice(0, 180)}
          </p>
        )}

        {!sections ? (
          <p className="text-xs text-gray-400">
            {busy
              ? "Reading this post and writing the analysis…"
              : "Not analysed yet — press Analyse to read this post on its own."}
          </p>
        ) : (
          <div className="space-y-5">
            <Section n={1} title="Executive summary"><Prose text={sections.exec_summary} /></Section>
            <Section n={2} title="So what — strategic implications">
              <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
                <Prose text={sections.so_what} />
              </div>
            </Section>
            <Section n={3} title="What is being said"><Prose text={sections.what_is_said} /></Section>
            <Section n={4} title="Voice — who is speaking">
              {sections.voice && (
                <p className="text-xs text-gray-500 dark:text-[#94a3b8] mb-1.5">
                  Classified as{" "}
                  <span className="font-semibold text-gray-700 dark:text-[#e2e8f0]">
                    {BUCKET_LABEL[sections.voice.bucket] ?? sections.voice.bucket}
                  </span>{" "}
                  ({sections.voice.confidence}
                  {sections.voice.evidence ? ` · ${sections.voice.evidence}` : ""})
                </p>
              )}
              <Prose text={sections.voice_note} />
            </Section>
            <Section n={5} title="Reach">
              <ReachBlock reach={sections.reach} />
              <Prose text={sections.reach_note} />
            </Section>
            <Section n={6} title="Key sub-topics to consider">
              <SubtopicList items={sections.subtopics ?? []} />
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}
