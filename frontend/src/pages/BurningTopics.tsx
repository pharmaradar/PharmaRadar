import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays, ChevronDown, ChevronUp, Loader2, Pencil, Plus, RefreshCw, Trash2, Zap,
} from "lucide-react";
import { api, BurningTopic, Congress as CongressType } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";
import ReportView, { StatusBadge } from "@/components/ReportView";
import QuestionEditor from "@/components/QuestionEditor";

const LANGS = [
  { value: "", label: "All languages" },
  { value: "fr", label: "French" },
  { value: "en", label: "English" },
  { value: "de", label: "German" },
  { value: "es", label: "Spanish" },
  { value: "it", label: "Italian" },
];

const EMPTY_TOPIC_FORM = {
  name: "", description: "", period_days: 30, language_filter: "",
  restriction_terms: "", exclusion_words: "",
};

const EMPTY_CONGRESS_FORM = {
  name: "", hashtags: "", start_date: "", end_date: "", disease_area: "",
};

function splitValues(raw: string): string[] {
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

function formatDate(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric",
  });
}

type Entry =
  | { kind: "topic"; data: BurningTopic }
  | { kind: "congress"; data: CongressType };

function KindBadge({ kind }: { kind: "topic" | "congress" }) {
  return kind === "topic" ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-purple-50 text-purple-600 dark:bg-purple-900/20 dark:text-purple-400">
      <Zap size={11} /> Topic
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400">
      <CalendarDays size={11} /> Congress
    </span>
  );
}

function EntryMeta({ entry }: { entry: Entry }) {
  if (entry.kind === "topic") {
    const t = entry.data;
    return (
      <>
        {t.description && (
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5 line-clamp-2">{t.description}</p>
        )}
        <div className="flex flex-wrap gap-1.5 mt-2 text-xs text-slate-400">
          <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">last {t.period_days}d</span>
          {t.language_filter && (
            <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 uppercase">{t.language_filter}</span>
          )}
          {t.restriction_terms.length > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">+{t.restriction_terms.join(", ")}</span>
          )}
          {t.exclusion_words.length > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5">−{t.exclusion_words.join(", ")}</span>
          )}
        </div>
      </>
    );
  }
  const c = entry.data;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 mt-1 text-sm text-slate-500 dark:text-slate-400">
        <CalendarDays size={14} />
        <span>{formatDate(c.start_date)} – {formatDate(c.end_date)}</span>
        {c.disease_area && <span className="text-slate-400">{c.disease_area}</span>}
      </div>
      {c.hashtags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {c.hashtags.map((h) => (
            <span key={h} className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-xs text-slate-500">
              {h.startsWith("#") ? h : `#${h}`}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

function EntryCard({ entry, onEdit }: { entry: Entry; onEdit: (entry: Entry) => void }) {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  // Congresses have no owner concept (no created_by column) — any logged-in user manages them.
  const canEdit = entry.kind === "topic" ? (isAdmin || entry.data.created_by === user?.id) : true;
  const [expanded, setExpanded] = useState(false);
  const [showOlder, setShowOlder] = useState(false);
  const id = entry.data.id;
  const listKey = entry.kind === "topic" ? "burning-topics" : "congress";

  const { data: reports } = useQuery({
    queryKey: ["entry-reports", entry.kind, id],
    queryFn: () => entry.kind === "topic" ? api.burningTopics.reports(id) : api.congress.reports(id),
    enabled: expanded,
    // Same 3s polling pattern as the Settings pipeline status
    refetchInterval: (q) =>
      q.state.data?.some((r) => r.status === "pending" || r.status === "running") ? 3000 : false,
  });

  // Date filter: overrides the topic's window for this generation only, so the
  // team can re-cut a report over a different period without editing the topic.
  // A congress has fixed dates, so the control is topic-only.
  const [periodDays, setPeriodDays] = useState<number | undefined>(undefined);

  const generateMut = useMutation({
    mutationFn: () => entry.kind === "topic"
      ? api.burningTopics.generate(id, periodDays)
      : api.congress.generate(id),
    onSuccess: () => {
      setExpanded(true);
      qc.invalidateQueries({ queryKey: ["entry-reports", entry.kind, id] });
      qc.invalidateQueries({ queryKey: [listKey] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => entry.kind === "topic" ? api.burningTopics.remove(id) : api.congress.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: [listKey] }),
  });

  const inFlight = generateMut.isPending
    || reports?.some((r) => r.status === "pending" || r.status === "running")
    || (!expanded && (entry.data.latest_report?.status === "pending" || entry.data.latest_report?.status === "running"));
  const latest = reports?.[0];
  const older = reports?.slice(1) ?? [];

  return (
    <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 shadow-sm overflow-hidden">
      <div className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-semibold text-slate-800 dark:text-slate-100">{entry.data.name}</h3>
              <KindBadge kind={entry.kind} />
              {!entry.data.is_active && (
                <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-500">Inactive</span>
              )}
              {entry.data.latest_report && !expanded && <StatusBadge status={entry.data.latest_report.status} />}
            </div>
            <EntryMeta entry={entry} />
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {entry.kind === "topic" && (
              <select
                value={periodDays ?? ""}
                onChange={(e) => setPeriodDays(e.target.value ? Number(e.target.value) : undefined)}
                disabled={Boolean(inFlight)}
                title="Period covered by the next report"
                className="px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-white/10 bg-transparent text-slate-600 dark:text-slate-300 disabled:opacity-50"
              >
                <option value="" className="dark:bg-[#0d1424]">
                  Default ({entry.data.period_days}d)
                </option>
                {[7, 30, 90, 180, 365].map((d) => (
                  <option key={d} value={d} className="dark:bg-[#0d1424]">Last {d} days</option>
                ))}
              </select>
            )}
            <button
              onClick={() => generateMut.mutate()}
              disabled={Boolean(inFlight) || !entry.data.is_active}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-pharma-blue text-white rounded-lg text-xs font-medium hover:bg-pharma-light disabled:opacity-50"
            >
              {inFlight ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              {inFlight ? "Generating…" : "Generate report"}
            </button>
            {canEdit && (
              <>
                <button
                  onClick={() => onEdit(entry)}
                  className="p-1.5 text-slate-400 hover:text-pharma-blue"
                  title={`Edit ${entry.kind}`}
                >
                  <Pencil size={15} />
                </button>
                <button
                  onClick={() => { if (confirm(`Delete "${entry.data.name}" and its reports?`)) deleteMut.mutate(); }}
                  className="p-1.5 text-slate-400 hover:text-red-500"
                  title={`Delete ${entry.kind}`}
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
        <>
          {entry.kind === "congress" && <QuestionEditor congress={entry.data} />}
          <div className="border-t border-slate-200/60 dark:border-white/10 px-5 py-4">
            {!reports ? (
              <div className="text-sm text-slate-400 py-2">Loading reports…</div>
            ) : reports.length === 0 ? (
              <div className="text-sm text-slate-400 py-2">
                {entry.kind === "congress"
                  ? "No reports yet — add questions, then generate the first report."
                  : "No reports yet — hit “Generate report” to create the first one."}
              </div>
            ) : (
              <>
                {latest && <ReportView scope={entry.kind} scopeId={id} report={latest} />}
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
                            <ReportView scope={entry.kind} scopeId={id} report={r} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function BurningTopics() {
  const qc = useQueryClient();
  // Poll the lists ONLY while some report is actually cooking — the badges are
  // static otherwise, and unconditional polling hammered two endpoints every
  // 15s for every user parked on this page.
  const inFlightPoll = (data?: { latest_report: { status: string } | null }[]) =>
    data?.some((e) => e.latest_report && ["pending", "running"].includes(e.latest_report.status))
      ? 5000 : false as const;

  const { data: topics, isLoading: topicsLoading } = useQuery({
    queryKey: ["burning-topics"],
    queryFn: api.burningTopics.list,
    refetchInterval: (q) => inFlightPoll(q.state.data),
  });
  const { data: congresses, isLoading: congressesLoading } = useQuery({
    queryKey: ["congress"],
    queryFn: api.congress.list,
    refetchInterval: (q) => inFlightPoll(q.state.data),
  });

  const entries: Entry[] = useMemo(() => {
    const t: Entry[] = (topics ?? []).map((data) => ({ kind: "topic" as const, data }));
    const c: Entry[] = (congresses ?? []).map((data) => ({ kind: "congress" as const, data }));
    return [...t, ...c].sort((a, b) => b.data.created_at.localeCompare(a.data.created_at));
  }, [topics, congresses]);

  const [filter, setFilter] = useState<"all" | "topic" | "congress">("all");
  const filtered = filter === "all" ? entries : entries.filter((e) => e.kind === filter);

  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Entry | null>(null);
  const [formKind, setFormKind] = useState<"topic" | "congress">("topic");
  const [topicForm, setTopicForm] = useState(EMPTY_TOPIC_FORM);
  const [congressForm, setCongressForm] = useState(EMPTY_CONGRESS_FORM);

  const openNew = () => {
    setEditing(null);
    setFormKind("topic");
    setTopicForm(EMPTY_TOPIC_FORM);
    setCongressForm(EMPTY_CONGRESS_FORM);
    setShowForm(true);
  };

  const openEdit = (entry: Entry) => {
    setEditing(entry);
    setFormKind(entry.kind);
    if (entry.kind === "topic") {
      const t = entry.data;
      setTopicForm({
        name: t.name,
        description: t.description || "",
        period_days: t.period_days,
        language_filter: t.language_filter || "",
        restriction_terms: t.restriction_terms.join(", "),
        exclusion_words: t.exclusion_words.join(", "),
      });
    } else {
      const c = entry.data;
      setCongressForm({
        name: c.name,
        hashtags: c.hashtags.join(", "),
        start_date: c.start_date,
        end_date: c.end_date,
        disease_area: c.disease_area || "",
      });
    }
    setShowForm(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const closeForm = () => { setShowForm(false); setEditing(null); };

  const saveMut = useMutation({
    mutationFn: (): Promise<BurningTopic | CongressType> => {
      if (formKind === "topic") {
        const body = {
          name: topicForm.name.trim(),
          description: topicForm.description.trim() || null,
          language_filter: topicForm.language_filter || null,
          period_days: topicForm.period_days,
          restriction_terms: splitValues(topicForm.restriction_terms),
          exclusion_words: splitValues(topicForm.exclusion_words),
        };
        return editing ? api.burningTopics.update(editing.data.id, body) : api.burningTopics.create(body);
      }
      const body = {
        name: congressForm.name.trim(),
        hashtags: splitValues(congressForm.hashtags),
        start_date: congressForm.start_date,
        end_date: congressForm.end_date,
        disease_area: congressForm.disease_area.trim() || null,
      };
      return editing ? api.congress.update(editing.data.id, body) : api.congress.create(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [formKind === "topic" ? "burning-topics" : "congress"] });
      closeForm();
    },
  });

  const dateError = Boolean(
    formKind === "congress" && congressForm.start_date && congressForm.end_date
    && congressForm.start_date > congressForm.end_date
  );

  const saveDisabled = saveMut.isPending || (
    formKind === "topic"
      ? topicForm.name.trim().length < 2
      : congressForm.name.trim().length < 2 || !congressForm.start_date || !congressForm.end_date || dateError
  );

  const isLoading = topicsLoading || congressesLoading;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Zap size={22} className="text-pharma-blue dark:text-blue-300" />
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Burning Topics</h1>
        </div>
        <button
          onClick={openNew}
          className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light"
        >
          <Plus size={16} /> New
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 -mt-2">
        <p className="text-sm text-slate-500 dark:text-slate-400 max-w-2xl">
          Track a theme over time, or monitor a congress — both produce an AI-synthesized report
          with key findings, so-what and a downloadable PDF.
        </p>
        <div className="inline-flex rounded-lg border border-slate-200 dark:border-[#1e3a5f] p-0.5 shrink-0">
          {(["all", "topic", "congress"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-3 py-1.5 text-xs rounded-md capitalize transition-colors",
                filter === f ? "bg-pharma-blue text-white" : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-200"
              )}
            >
              {f === "all" ? "All" : f === "topic" ? "Topics" : "Congresses"}
            </button>
          ))}
        </div>
      </div>

      {showForm && (
        <div className="glass-panel rounded-xl p-5 shadow-sm border border-slate-200/50 dark:border-white/10">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <h2 className="font-semibold">
              {editing ? `Edit ${formKind === "topic" ? "Topic" : "Congress"}` : "New entry"}
            </h2>
            {!editing && (
              <div className="inline-flex rounded-lg border border-slate-200 dark:border-[#1e3a5f] p-0.5">
                <button
                  type="button"
                  onClick={() => setFormKind("topic")}
                  className={cn("px-3 py-1.5 text-xs rounded-md", formKind === "topic" ? "bg-pharma-blue text-white" : "text-slate-500")}
                >
                  Recurring Topic
                </button>
                <button
                  type="button"
                  onClick={() => setFormKind("congress")}
                  className={cn("px-3 py-1.5 text-xs rounded-md", formKind === "congress" ? "bg-pharma-blue text-white" : "text-slate-500")}
                >
                  Congress Event
                </button>
              </div>
            )}
          </div>

          {formKind === "topic" ? (
            <div className="grid gap-3">
              <input
                placeholder="Topic name (e.g. subcutaneous administration)"
                value={topicForm.name}
                onChange={(e) => setTopicForm((f) => ({ ...f, name: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
              <textarea
                placeholder="Description — what exactly to track (helps the AI focus)"
                value={topicForm.description}
                onChange={(e) => setTopicForm((f) => ({ ...f, description: e.target.value }))}
                rows={2}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent resize-none"
              />
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Period (days)
                  <input
                    type="number" min={1} max={365}
                    value={topicForm.period_days}
                    onChange={(e) => setTopicForm((f) => ({ ...f, period_days: Math.max(1, Number(e.target.value) || 30) }))}
                    className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Language filter
                  <select
                    value={topicForm.language_filter}
                    onChange={(e) => setTopicForm((f) => ({ ...f, language_filter: e.target.value }))}
                    className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent dark:bg-[#0f1e38]"
                  >
                    {LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
                  </select>
                </label>
              </div>
              <input
                placeholder="Extra match terms, comma-separated (e.g. SC injection, subcut)"
                value={topicForm.restriction_terms}
                onChange={(e) => setTopicForm((f) => ({ ...f, restriction_terms: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
              <input
                placeholder="Exclusion words, comma-separated — posts containing these are ignored"
                value={topicForm.exclusion_words}
                onChange={(e) => setTopicForm((f) => ({ ...f, exclusion_words: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
            </div>
          ) : (
            <div className="grid gap-3">
              <input
                placeholder="Congress name (e.g. ASCO 2026)"
                value={congressForm.name}
                onChange={(e) => setCongressForm((f) => ({ ...f, name: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
              <input
                placeholder="Hashtags, comma-separated (e.g. #ASCO26, ASCO2026)"
                value={congressForm.hashtags}
                onChange={(e) => setCongressForm((f) => ({ ...f, hashtags: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="text-xs text-slate-400">
                  Start date
                  <input
                    type="date"
                    value={congressForm.start_date}
                    onChange={(e) => setCongressForm((f) => ({ ...f, start_date: e.target.value }))}
                    className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  End date
                  <input
                    type="date"
                    value={congressForm.end_date}
                    onChange={(e) => setCongressForm((f) => ({ ...f, end_date: e.target.value }))}
                    className="w-full mt-1 px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
                  />
                </label>
              </div>
              {dateError && <div className="text-xs text-red-500">End date must be on or after the start date.</div>}
              <input
                placeholder="Disease area (optional)"
                value={congressForm.disease_area}
                onChange={(e) => setCongressForm((f) => ({ ...f, disease_area: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
            </div>
          )}

          {saveMut.isError && (
            <div className="text-xs text-red-500 mt-3">{(saveMut.error as Error)?.message}</div>
          )}
          <div className="flex gap-2 justify-end mt-3">
            <button onClick={closeForm} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">Cancel</button>
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveDisabled}
              className="px-4 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50"
            >
              {editing ? "Save changes" : formKind === "topic" ? "Create topic" : "Create congress"}
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-slate-400">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="glass-panel rounded-xl border border-slate-200/50 dark:border-white/10 py-14 text-center">
          <Zap size={28} className="mx-auto text-slate-300 dark:text-slate-600 mb-3" />
          <div className="text-slate-500 dark:text-slate-400 text-sm">
            {filter === "congress" ? "No congresses yet." : filter === "topic" ? "No burning topics yet." : "Nothing tracked yet."}
            {" "}Create one to get started.
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map((entry) => (
            <EntryCard key={`${entry.kind}-${entry.data.id}`} entry={entry} onEdit={openEdit} />
          ))}
        </div>
      )}
    </div>
  );
}
