import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown, ChevronUp, Download, ExternalLink, Loader2, Pencil, Plus,
  RefreshCw, Send, Trash2, Zap,
} from "lucide-react";
import { api, BurningTopic, BurningTopicReport } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

const LANGS = [
  { value: "", label: "All languages" },
  { value: "fr", label: "French" },
  { value: "en", label: "English" },
  { value: "de", label: "German" },
  { value: "es", label: "Spanish" },
  { value: "it", label: "Italian" },
];

const EMPTY_FORM = {
  name: "", description: "", period_days: 30, language_filter: "",
  restriction_terms: "", exclusion_words: "",
};

function splitTerms(raw: string): string[] {
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

function StatusBadge({ status }: { status: string }) {
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
    mutationFn: (q: string) => api.burningTopics.followup(topicId, report.id, q, thread),
    onSuccess: (res, q) => {
      setThread((t) => [...t, { role: "user", content: q }, { role: "assistant", content: res.answer }]);
      setQuestion("");
    },
  });

  return (
    <div className="mt-4 border-t border-slate-200/60 dark:border-white/10 pt-4">
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
        Ask a follow-up about this report
      </div>
      {thread.length > 0 && (
        <div className="space-y-2 mb-3 max-h-72 overflow-y-auto pr-1">
          {thread.map((m, i) => (
            <div key={i} className={cn(
              "text-sm rounded-lg px-3 py-2 whitespace-pre-wrap",
              m.role === "user"
                ? "bg-pharma-blue/10 text-slate-800 dark:text-slate-100 ml-8"
                : "bg-slate-100 dark:bg-white/5 text-slate-700 dark:text-slate-200 mr-8"
            )}>
              {m.content}
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
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && question.trim() && !askMut.isPending) askMut.mutate(question.trim()); }}
          placeholder="e.g. Which post drove the most engagement, and why?"
          className="flex-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
          disabled={askMut.isPending}
        />
        <button
          onClick={() => question.trim() && askMut.mutate(question.trim())}
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

function ReportView({ topicId, report }: { topicId: number; report: BurningTopicReport }) {
  const [downloading, setDownloading] = useState(false);

  const downloadPdf = async () => {
    if (report.pdf_url) { window.open(report.pdf_url, "_blank"); return; }
    setDownloading(true);
    try {
      const blob = await api.burningTopics.downloadPdf(topicId, report.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `burning_topic_${topicId}_report_${report.id}.pdf`;
      a.click();
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
        Generating report — querying data, searching the web, synthesizing… (updates every 3s)
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

      {report.summary_md && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Summary</div>
          <p className="text-sm text-slate-700 dark:text-slate-200 whitespace-pre-wrap leading-relaxed">
            {report.summary_md}
          </p>
        </div>
      )}

      {report.key_findings.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Key findings</div>
          <ul className="space-y-1.5">
            {report.key_findings.map((f, i) => (
              <li key={i} className="text-sm text-slate-700 dark:text-slate-200 flex gap-2">
                <span className="text-pharma-blue shrink-0 mt-0.5">•</span>{f}
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

      {report.important_posts.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Important posts</div>
          <div className="space-y-2">
            {report.important_posts.map((p, i) => (
              <a key={i} href={p.url} target="_blank" rel="noreferrer"
                 className="block rounded-lg border border-slate-200/60 dark:border-white/10 px-3 py-2 hover:border-pharma-blue/40 transition-colors group">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate group-hover:text-pharma-blue">
                      {p.title || p.url}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {p.author || "?"} · {p.platform || "web"} · {p.engagement.toLocaleString()} engagement
                    </div>
                    {p.why && <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">{p.why}</div>}
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
            {report.main_authors.map((a, i) => (
              <div key={i} className="rounded-lg bg-slate-50 dark:bg-white/5 border border-slate-200/60 dark:border-white/10 px-3 py-1.5"
                   title={a.note || undefined}>
                <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{a.author}</span>
                <span className="text-xs text-slate-400 ml-2">
                  {a.posts} post{a.posts > 1 ? "s" : ""} · {a.engagement.toLocaleString()} eng
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <FollowupBox topicId={topicId} report={report} />
    </div>
  );
}

function TopicCard({ topic }: { topic: BurningTopic }) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "admin" || topic.created_by === user?.id;
  const [expanded, setExpanded] = useState(false);
  const [showOlder, setShowOlder] = useState(false);

  const { data: reports } = useQuery({
    queryKey: ["bt-reports", topic.id],
    queryFn: () => api.burningTopics.reports(topic.id),
    enabled: expanded,
    // Same 3s polling pattern as the Settings pipeline status
    refetchInterval: (q) =>
      q.state.data?.some((r) => r.status === "pending" || r.status === "running") ? 3000 : false,
  });

  const generateMut = useMutation({
    mutationFn: () => api.burningTopics.generate(topic.id),
    onSuccess: () => {
      setExpanded(true);
      qc.invalidateQueries({ queryKey: ["bt-reports", topic.id] });
      qc.invalidateQueries({ queryKey: ["burning-topics"] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => api.burningTopics.remove(topic.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["burning-topics"] }),
  });

  const inFlight = generateMut.isPending
    || reports?.some((r) => r.status === "pending" || r.status === "running")
    || (!expanded && (topic.latest_report?.status === "pending" || topic.latest_report?.status === "running"));
  const latest = reports?.[0];
  const older = reports?.slice(1) ?? [];

  return (
    <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 shadow-sm overflow-hidden">
      <div className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-slate-800 dark:text-slate-100">{topic.name}</h3>
              {!topic.is_active && (
                <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-500">Inactive</span>
              )}
              {topic.latest_report && !expanded && <StatusBadge status={topic.latest_report.status} />}
            </div>
            {topic.description && (
              <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5 line-clamp-2">{topic.description}</p>
            )}
            <div className="flex flex-wrap gap-1.5 mt-2 text-xs text-slate-400">
              <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">last {topic.period_days}d</span>
              {topic.language_filter && (
                <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 uppercase">{topic.language_filter}</span>
              )}
              {topic.restriction_terms.length > 0 && (
                <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">
                  +{topic.restriction_terms.join(", ")}
                </span>
              )}
              {topic.exclusion_words.length > 0 && (
                <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">
                  −{topic.exclusion_words.join(", ")}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => generateMut.mutate()}
              disabled={inFlight || !topic.is_active}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-pharma-blue text-white rounded-lg text-xs font-medium hover:bg-pharma-light disabled:opacity-50"
            >
              {inFlight ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              {inFlight ? "Generating…" : "Generate report"}
            </button>
            {canEdit && (
              <>
                <button
                  onClick={() => window.dispatchEvent(new CustomEvent("bt-edit", { detail: topic }))}
                  className="p-1.5 text-slate-400 hover:text-pharma-blue" title="Edit topic"
                >
                  <Pencil size={15} />
                </button>
                <button
                  onClick={() => { if (confirm(`Delete topic "${topic.name}" and all its reports?`)) deleteMut.mutate(); }}
                  className="p-1.5 text-slate-400 hover:text-red-500" title="Delete topic"
                >
                  <Trash2 size={15} />
                </button>
              </>
            )}
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              title={expanded ? "Collapse" : "View reports"}
            >
              {expanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
            </button>
          </div>
        </div>

        {generateMut.isError && (
          <div className="text-xs text-red-500 mt-2">
            {String((generateMut.error as Error)?.message || "").replace(/^\d+:\s*/, "").slice(0, 300)}
          </div>
        )}
      </div>

      {expanded && (
        <div className="border-t border-slate-200/60 dark:border-white/10 px-5 py-4">
          {!reports ? (
            <div className="text-sm text-slate-400 py-2">Loading reports…</div>
          ) : reports.length === 0 ? (
            <div className="text-sm text-slate-400 py-2">
              No reports yet — hit “Generate report” to create the first one.
            </div>
          ) : (
            <>
              {latest && <ReportView topicId={topic.id} report={latest} />}
              {older.length > 0 && (
                <div className="mt-4">
                  <button
                    onClick={() => setShowOlder(!showOlder)}
                    className="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                  >
                    {showOlder ? "Hide" : "Show"} {older.length} older report{older.length > 1 ? "s" : ""}
                  </button>
                  {showOlder && (
                    <div className="mt-3 space-y-6">
                      {older.map((r) => (
                        <div key={r.id} className="rounded-lg border border-slate-200/60 dark:border-white/10 p-4">
                          <div className="mb-2"><StatusBadge status={r.status} /></div>
                          <ReportView topicId={topic.id} report={r} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function BurningTopics() {
  const qc = useQueryClient();
  const { data: topics, isLoading } = useQuery({
    queryKey: ["burning-topics"],
    queryFn: api.burningTopics.list,
    refetchInterval: 15000,   // keeps latest-report badges fresh while a report cooks
  });

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  // TopicCard edit buttons dispatch a window event so the form state can live up here
  useEffect(() => {
    const onEdit = (e: Event) => {
      const t = (e as CustomEvent<BurningTopic>).detail;
      setEditingId(t.id);
      setForm({
        name: t.name,
        description: t.description || "",
        period_days: t.period_days,
        language_filter: t.language_filter || "",
        restriction_terms: t.restriction_terms.join(", "),
        exclusion_words: t.exclusion_words.join(", "),
      });
      setShowForm(true);
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    window.addEventListener("bt-edit", onEdit);
    return () => window.removeEventListener("bt-edit", onEdit);
  }, []);

  const closeForm = () => { setShowForm(false); setEditingId(null); setForm(EMPTY_FORM); };

  const saveMut = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        language_filter: form.language_filter || null,
        period_days: form.period_days,
        restriction_terms: splitTerms(form.restriction_terms),
        exclusion_words: splitTerms(form.exclusion_words),
      };
      return editingId
        ? api.burningTopics.update(editingId, body)
        : api.burningTopics.create(body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["burning-topics"] }); closeForm(); },
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Zap size={22} className="text-pharma-blue dark:text-blue-300" />
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Burning Topics</h1>
        </div>
        <button
          onClick={() => { closeForm(); setShowForm(true); }}
          className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light"
        >
          <Plus size={16} /> New Topic
        </button>
      </div>

      <p className="text-sm text-slate-500 dark:text-slate-400 -mt-2">
        Persistent topics tracked over time. Each report combines what's already in the database
        with a fresh web search, synthesized into key findings, so-what and top posts — with a PDF.
      </p>

      {showForm && (
        <div className="glass-panel rounded-xl p-5 shadow-sm border border-slate-200/50 dark:border-white/10">
          <h2 className="font-semibold mb-4">{editingId ? "Edit Topic" : "New Topic"}</h2>
          <div className="grid gap-3">
            <input
              placeholder="Topic name (e.g. subcutaneous administration)"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
            />
            <textarea
              placeholder="Description — what exactly to track (helps the AI focus)"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              rows={2}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent resize-none"
            />
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-slate-400">
                Period (days)
                <input
                  type="number" min={1} max={365}
                  value={form.period_days}
                  onChange={(e) => setForm((f) => ({ ...f, period_days: Math.max(1, Number(e.target.value) || 30) }))}
                  className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
                />
              </label>
              <label className="text-xs text-slate-400">
                Language filter
                <select
                  value={form.language_filter}
                  onChange={(e) => setForm((f) => ({ ...f, language_filter: e.target.value }))}
                  className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent dark:bg-[#0f1e38]"
                >
                  {LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
                </select>
              </label>
            </div>
            <input
              placeholder="Extra match terms, comma-separated (e.g. SC injection, subcut)"
              value={form.restriction_terms}
              onChange={(e) => setForm((f) => ({ ...f, restriction_terms: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
            />
            <input
              placeholder="Exclusion words, comma-separated — posts containing these are ignored"
              value={form.exclusion_words}
              onChange={(e) => setForm((f) => ({ ...f, exclusion_words: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
            />
            {saveMut.isError && (
              <div className="text-xs text-red-500">{(saveMut.error as Error)?.message}</div>
            )}
            <div className="flex gap-2 justify-end">
              <button onClick={closeForm} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">Cancel</button>
              <button
                onClick={() => saveMut.mutate()}
                disabled={form.name.trim().length < 2 || saveMut.isPending}
                className="px-4 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50"
              >
                {editingId ? "Save changes" : "Create topic"}
              </button>
            </div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-slate-400">Loading…</div>
      ) : !topics || topics.length === 0 ? (
        <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 py-14 text-center">
          <Zap size={28} className="mx-auto text-slate-300 dark:text-slate-600 mb-3" />
          <div className="text-slate-500 dark:text-slate-400 text-sm">
            No burning topics yet. Create one to start tracking a theme over time.
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {topics.map((t) => <TopicCard key={t.id} topic={t} />)}
        </div>
      )}
    </div>
  );
}
