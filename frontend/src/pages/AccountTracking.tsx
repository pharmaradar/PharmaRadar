import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, AtSign, Check, ExternalLink, Eye, Facebook, Heart,
  Instagram, Linkedin, Loader2, MessageCircle, Plus, RefreshCw, Search,
  Pencil, Sparkles, Trash2, Twitter, X,
} from "lucide-react";
import {
  api, type AccountPost, type TrackedAccountFull,
} from "@/lib/api";
import { Prose, SubtopicList, VoiceChart, VolumeBlock } from "@/components/MarketSections";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

/**
 * Account Tracking — the accounts the platform collects directly.
 *
 * Keyword search pays for N results and hopes some are relevant. A named
 * account is on-topic and from a known voice by construction, which is why the
 * client asked for this and why it is the highest-yield lever on French volume.
 *
 * This has its own page and its own pipeline (tasks/accounts.py) rather than a
 * panel on the Social page: tracking a named account is not a setting of the
 * keyword scan, and it should not queue behind it or fail when it fails.
 */

const PLATFORM_META: Record<string, { label: string; icon: React.ElementType; tint: string }> = {
  twitter:   { label: "X / Twitter", icon: Twitter,   tint: "text-sky-500" },
  linkedin:  { label: "LinkedIn",    icon: Linkedin,  tint: "text-blue-600 dark:text-blue-400" },
  instagram: { label: "Instagram",   icon: Instagram, tint: "text-pink-500" },
  facebook:  { label: "Facebook",    icon: Facebook,  tint: "text-indigo-500" },
};

/** What it costs to collect a platform, stated where accounts are added — the
 *  free lanes run on every sweep, the billed ones only when Apify is set up. */
const PLATFORM_COST: Record<string, string> = {
  twitter:   "Collected free on every sweep.",
  linkedin:  "Collected free on every sweep.",
  instagram: "Needs Apify — billed per refresh.",
  facebook:  "Needs Apify — billed per refresh.",
};

const ROLE_LABELS: Record<string, string> = {
  kol: "KOL",
  institution: "Institution",
  pharma: "Pharma",
  patient_association: "Patient association",
  media: "Media",
  other: "Other",
};

function relative(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

/* ─── per-account detail ─────────────────────────────────── */

const PLATFORM_TINT: Record<string, string> = {
  twitter: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  linkedin: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300",
  instagram: "bg-pink-100 text-pink-700 dark:bg-pink-500/15 dark:text-pink-300",
  facebook: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
};

function Stat({ icon: Icon, value }: { icon: React.ElementType; value: number }) {
  if (!value) return null;
  return (
    <span className="flex items-center gap-1 text-[11px] text-gray-400">
      <Icon size={11} /> {value}
    </span>
  );
}

/** One post, in the same shape as the Social Trends card so the two pages read
 *  the same way.
 *
 *  The image is optional by necessity, not by style: only Instagram and
 *  Facebook return one. X and LinkedIn arrive through search, which carries no
 *  media at all, so the card has to look deliberate without it. */
function PostCard({ post, onClick }: { post: AccountPost; onClick: () => void }) {
  const stamp = post.posted_at
    ? `posted ${new Date(post.posted_at).toLocaleDateString()}`
    : `collected ${post.collected_at ? new Date(post.collected_at).toLocaleDateString() : "—"}`;

  return (
    <div onClick={onClick}
      className="glass-panel rounded-xl overflow-hidden cursor-pointer hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 flex flex-col">
      {post.thumbnail_url && (
        <div className="h-32 bg-slate-100 dark:bg-slate-800 overflow-hidden shrink-0">
          <img src={post.thumbnail_url} alt="" loading="lazy"
            className="w-full h-full object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
        </div>
      )}
      <div className="p-3 flex-1 flex flex-col">
        <div className="flex items-center gap-2 mb-1.5">
          <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium capitalize",
            PLATFORM_TINT[post.platform] ?? "bg-gray-100 text-gray-600")}>
            {post.platform}
          </span>
          <span className="text-[10px] text-gray-400 truncate">{stamp}</span>
        </div>
        <p className="text-xs text-gray-600 dark:text-[#94a3b8] line-clamp-4 flex-1">
          {post.text || <span className="italic text-gray-400">No text captured</span>}
        </p>
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-gray-100 dark:border-slate-800">
          <Stat icon={Heart} value={post.likes} />
          <Stat icon={MessageCircle} value={post.comments} />
          <Stat icon={Eye} value={post.views} />
          <a href={post.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
            className="ml-auto text-gray-400 hover:text-pharma-blue" title="Open post">
            <ExternalLink size={11} />
          </a>
        </div>
      </div>
    </div>
  );
}

/** The AI read of an account: what they talk about and what it means for us. */

/** Inline editor for one account.
 *
 *  Everything except the platform can change: the handle is what the scrapers
 *  query, and correcting a wrong one is the single most valuable edit here —
 *  every French Facebook page in this registry was originally a wrong slug
 *  producing nothing. Platform is fixed because a handle is not portable
 *  between platforms, so changing it would mean a different account entirely.
 */
function EditAccount({ account, onDone, onCancel }: {
  account: TrackedAccountFull; onDone: () => void; onCancel: () => void;
}) {
  const [form, setForm] = useState({
    handle: account.handle,
    label: account.label ?? "",
    full_name: account.full_name ?? "",
    role: account.role ?? "",
    notes: account.notes ?? "",
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const saveMut = useMutation({
    mutationFn: () => api.accounts.update(account.id, {
      handle: form.handle.trim(),
      label: form.label.trim() || null,
      full_name: form.full_name.trim() || null,
      role: form.role || null,
      notes: form.notes.trim() || null,
    }),
    onSuccess: onDone,
  });

  const handleChanged = form.handle.trim() !== account.handle;

  return (
    <div className="rounded-xl border border-slate-200/60 dark:border-white/10 p-4 space-y-3"
      onClick={(e) => e.stopPropagation()}>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Handle or profile URL</span>
          <input value={form.handle} onChange={(e) => set("handle", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Display name</span>
          <input value={form.label} onChange={(e) => set("label", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Real name</span>
          <input value={form.full_name} onChange={(e) => set("full_name", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Type</span>
          <select value={form.role} onChange={(e) => set("role", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">
            <option value="" className="dark:bg-[#0d1424]">—</option>
            {Object.entries(ROLE_LABELS).map(([value, label]) => (
              <option key={value} value={value} className="dark:bg-[#0d1424]">{label}</option>
            ))}
          </select>
        </label>
      </div>
      <input value={form.notes} onChange={(e) => set("notes", e.target.value)}
        placeholder="Why this account is tracked (optional)"
        className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />

      {handleChanged && (
        <p className="flex items-start gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
          <AlertTriangle size={12} className="shrink-0 mt-0.5" />
          Changing the handle points collection at a different page. Posts already
          collected stay attached to this account; press Refresh afterwards to check
          the new handle returns anything.
        </p>
      )}

      <div className="flex items-center gap-2">
        <button onClick={() => saveMut.mutate()}
          disabled={!form.handle.trim() || saveMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50">
          {saveMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
          Save
        </button>
        <button onClick={onCancel}
          className="px-3 py-1.5 text-sm border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5">
          Cancel
        </button>
        {saveMut.isError && (
          <span className="text-xs text-red-400">
            {(saveMut.error as Error)?.message?.includes("409")
              ? "That handle is already tracked on this platform."
              : (saveMut.error as Error)?.message?.slice(0, 90)}
          </span>
        )}
      </div>
    </div>
  );
}

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

function AnalysisPanel({ account, onGenerate, busy }: {
  account: TrackedAccountFull; onGenerate: () => void; busy: boolean;
}) {
  const a = account.analysis;
  const has = !!a?.summary;
  const sec = a?.sections?.exec_summary ? a.sections : null;

  // Staleness is stated, not implied: an analysis written from 12 of 22 posts
  // should not read as though it covers all of them.
  const staleNote = a?.stale ? (
    <p className="flex items-start gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
      <AlertTriangle size={12} className="shrink-0 mt-0.5" />
      Written from {a.post_count} posts; {account.post_count} collected since.
      Regenerate to include them.
    </p>
  ) : null;

  return (
    <div className="rounded-xl border border-slate-200/60 dark:border-white/10 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-pharma-blue dark:text-blue-300" />
          <h3 className="text-sm font-semibold text-gray-800 dark:text-[#e2e8f0]">AI analysis</h3>
        </div>
        <button onClick={onGenerate} disabled={busy || !account.post_count}
          title={account.post_count ? "Read this account's posts and summarise them"
                                    : "Nothing collected yet to analyse"}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 disabled:opacity-50 transition-colors">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
          {busy ? "Reading…" : has ? "Regenerate" : "Analyse"}
        </button>
      </div>

      {!has ? (
        <p className="text-xs text-gray-400">
          {account.post_count
            ? "Not analysed yet — this reads their posts and writes a market-research analysis of them."
            : "No posts collected yet, so there is nothing to analyse."}
        </p>
      ) : sec ? (
        <div className="space-y-5">
          {staleNote}
          <Section n={1} title="Executive summary"><Prose text={sec.exec_summary} /></Section>
          <Section n={2} title="So what — strategic implications">
            <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-4 py-3">
              <Prose text={sec.so_what} />
            </div>
          </Section>
          <Section n={3} title="What is being said"><Prose text={sec.what_is_said} /></Section>
          <Section n={4} title="Voice distribution">
            <VoiceChart rows={sec.voice_rows ?? []} exactShare={sec.voice_exact_share ?? 0} />
            <Prose text={sec.voices_note} />
          </Section>
          <Section n={5} title="Volume of mentions">
            <VolumeBlock volume={sec.volume ?? {}} />
            <Prose text={sec.volume_note} />
          </Section>
          <Section n={6} title="Key sub-topics to consider">
            <SubtopicList items={sec.subtopics ?? []} />
          </Section>
        </div>
      ) : (
        /* Analyses written before the six-section format. Regenerating upgrades
           them; until then the earlier shape still renders. */
        <div className="space-y-3">
          {staleNote}
          <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">
            {a!.summary}
          </p>
          {a!.so_what && (
            <div className="rounded-lg bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 px-3 py-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300 mb-1">
                So what
              </p>
              <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed whitespace-pre-wrap">
                {a!.so_what}
              </p>
            </div>
          )}
          {a!.themes?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {a!.themes.map((t) => (
                <span key={t} className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-gray-600 dark:text-[#94a3b8]">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AccountDetailPanel({ id, startEditing = false, onClose }: {
  id: number; startEditing?: boolean; onClose: () => void;
}) {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [days, setDays] = useState(90);
  const [editing, setEditing] = useState(startEditing);

  const { data, isLoading } = useQuery({
    queryKey: ["account-detail", id, days],
    queryFn: () => api.accounts.detail(id, days),
  });

  const analyseMut = useMutation({
    mutationFn: (refresh: boolean) => api.accounts.analyse(id, refresh),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account-detail", id] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const refreshMut = useMutation({
    mutationFn: () => api.accounts.refresh(id),
    onSuccess: () => {
      // The sweep runs in a worker, so the rows appear a moment later.
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["account-detail", id] });
        qc.invalidateQueries({ queryKey: ["accounts"] });
      }, 4000);
    },
  });

  const account = data?.account;
  const meta = account ? PLATFORM_META[account.platform] : null;
  const Icon = meta?.icon ?? AtSign;

  // Escape closes, and the page behind must not scroll while this is open.
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose} role="dialog" aria-modal="true">
      <div onClick={(e) => e.stopPropagation()}
        className="overlay-panel rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto p-6 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className="p-2 rounded-lg bg-slate-100 dark:bg-white/5 shrink-0">
              <Icon size={18} className={meta?.tint} />
            </div>
            <div className="min-w-0">
              <h2 className="text-lg font-bold text-pharma-blue dark:text-[#e2e8f0] truncate">
                {account?.label || account?.handle || "Loading…"}
              </h2>
              <p className="text-xs text-gray-400">
                @{account?.handle} · {meta?.label}
                {account?.full_name && ` · ${account.full_name}`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {isAdmin && (
              <button onClick={() => setEditing((v) => !v)}
                title="Edit this account's handle and details"
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
                <Pencil size={12} /> {editing ? "Close" : "Edit"}
              </button>
            )}
            {isAdmin && (
              <button onClick={() => refreshMut.mutate()} disabled={refreshMut.isPending}
                title="Collect this account's latest posts now"
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs bg-pharma-blue text-white rounded-lg hover:bg-pharma-light disabled:opacity-50 transition-colors">
                {refreshMut.isPending
                  ? <Loader2 size={12} className="animate-spin" />
                  : <RefreshCw size={12} />}
                Refresh
              </button>
            )}
            <button onClick={onClose} aria-label="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:bg-slate-100 dark:hover:bg-white/5">
              <X size={16} />
            </button>
          </div>
        </div>

        {refreshMut.isSuccess && (
          <p className="text-xs text-emerald-600 dark:text-emerald-400">
            Refresh queued — new posts appear here within a minute.
          </p>
        )}

        {editing && account && (
          <EditAccount account={account}
            onCancel={() => setEditing(false)}
            onDone={() => {
              setEditing(false);
              qc.invalidateQueries({ queryKey: ["account-detail", id] });
              qc.invalidateQueries({ queryKey: ["accounts"] });
            }} />
        )}

        {account?.notes && (
          <p className="text-sm text-gray-600 dark:text-[#94a3b8] p-3 rounded-lg bg-slate-50/60 dark:bg-white/5">
            {account.notes}
          </p>
        )}

        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "Posts collected", value: account?.post_count ?? 0 },
            { label: `In last ${days}d`, value: data?.stats.posts_in_window ?? 0 },
            { label: "Engagement", value: data?.stats.total_engagement ?? 0 },
          ].map((stat) => (
            <div key={stat.label} className="p-3 rounded-lg bg-slate-50/60 dark:bg-white/5">
              <p className="text-lg font-bold text-gray-800 dark:text-white tabular-nums">{stat.value}</p>
              <p className="text-[11px] text-gray-400">{stat.label}</p>
            </div>
          ))}
        </div>

        {account && (
          <AnalysisPanel account={account} busy={analyseMut.isPending}
            onGenerate={() => analyseMut.mutate(!!account.analysis?.summary)} />
        )}
        {analyseMut.isError && (
          <p className="text-xs text-red-400">
            {(analyseMut.error as Error)?.message?.slice(0, 160)}
          </p>
        )}

        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400">Window</span>
          {[30, 90, 365].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={cn("px-2 py-1 text-[11px] rounded-lg border transition-colors",
                days === d
                  ? "bg-pharma-blue text-white border-pharma-blue"
                  : "border-slate-200 dark:border-white/10 text-gray-500 hover:bg-slate-50 dark:hover:bg-white/5")}>
              {d}d
            </button>
          ))}
        </div>

        {isLoading ? (
          <p className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 size={14} className="animate-spin" /> Loading posts…
          </p>
        ) : !data?.posts.length ? (
          <div className="text-sm text-gray-400 space-y-2">
            <p>No posts collected in this window.</p>
            {account?.last_scan_status === "empty" && (
              <p className="flex items-start gap-2 text-amber-600 dark:text-amber-400">
                <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                The last scan ran and found nothing — usually the handle is
                slightly wrong. Check it against the account's profile URL.
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {data.posts.map((post) => (
              <PostCard key={post.id} post={post}
                onClick={() => window.open(post.url, "_blank", "noopener")} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── add form ───────────────────────────────────────────── */

const EMPTY_FORM = {
  platform: "twitter", handle: "", label: "", full_name: "",
  role: "institution", notes: "",
};

function AddAccount({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const createMut = useMutation({
    mutationFn: () => api.accounts.create({
      platform: form.platform as TrackedAccountFull["platform"],
      handle: form.handle.trim(),
      label: form.label.trim() || null,
      full_name: form.full_name.trim() || null,
      role: form.role || null,
      notes: form.notes.trim() || null,
    }),
    onSuccess: () => { setForm(EMPTY_FORM); onDone(); },
  });

  return (
    <div className="glass rounded-xl p-4 space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Platform</span>
          <select value={form.platform} onChange={(e) => set("platform", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">
            {Object.entries(PLATFORM_META).map(([value, m]) => (
              <option key={value} value={value} className="dark:bg-[#0d1424]">{m.label}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Handle or profile URL</span>
          <input value={form.handle} onChange={(e) => set("handle", e.target.value)}
            placeholder="@GustaveRoussy or a profile link"
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Display name</span>
          <input value={form.label} onChange={(e) => set("label", e.target.value)}
            placeholder="Gustave Roussy"
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </label>
        <label className="space-y-1">
          <span className="text-[11px] text-gray-400">Type</span>
          <select value={form.role} onChange={(e) => set("role", e.target.value)}
            className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">
            {Object.entries(ROLE_LABELS).map(([value, label]) => (
              <option key={value} value={value} className="dark:bg-[#0d1424]">{label}</option>
            ))}
          </select>
        </label>
      </div>
      <input value={form.notes} onChange={(e) => set("notes", e.target.value)}
        placeholder="Why this account is tracked (optional)"
        className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />

      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={() => createMut.mutate()}
          disabled={!form.handle.trim() || createMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50">
          {createMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
          Add and collect now
        </button>
        <span className="text-[11px] text-gray-400">{PLATFORM_COST[form.platform]}</span>
        {createMut.isError && (
          <span className="text-xs text-red-400">
            {(createMut.error as Error)?.message?.includes("409")
              ? "That account is already tracked."
              : (createMut.error as Error)?.message}
          </span>
        )}
      </div>
    </div>
  );
}

/* ─── page ───────────────────────────────────────────────── */

export default function AccountTracking() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  // Whether the modal should open straight into edit mode (card pencil) or on
  // the analysis (card body).
  const [editOnOpen, setEditOnOpen] = useState(false);
  const [platform, setPlatform] = useState("all");
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: api.accounts.list,
  });

  const { data: status } = useQuery({
    queryKey: ["accounts-status"],
    queryFn: api.accounts.status,
    // Poll only while a sweep is actually running.
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  });

  const scanMut = useMutation({
    mutationFn: api.accounts.scanAll,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts-status"] }),
  });
  const refreshMut = useMutation({
    mutationFn: (id: number) => api.accounts.refresh(id),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["accounts"] }), 4000),
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.accounts.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      api.accounts.update(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });

  const accounts = (data?.accounts ?? []).filter((a) => {
    if (platform !== "all" && a.platform !== platform) return false;
    const term = search.trim().toLowerCase();
    if (!term) return true;
    return [a.handle, a.label, a.full_name].some((v) => v?.toLowerCase().includes(term));
  });
  const totals = data?.totals;
  const running = status?.running;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-white">Account Tracking</h1>
          <p className="text-sm text-gray-400">
            Accounts collected directly — every post they publish, no keyword luck involved.
          </p>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2">
            <button onClick={() => setAdding(!adding)}
              className="flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
              {adding ? <X size={14} /> : <Plus size={14} />}
              {adding ? "Cancel" : "Add account"}
            </button>
            <button onClick={() => scanMut.mutate()} disabled={running || scanMut.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm bg-pharma-blue text-white rounded-lg hover:bg-pharma-light disabled:opacity-50 transition-colors">
              {running || scanMut.isPending
                ? <Loader2 size={14} className="animate-spin" />
                : <RefreshCw size={14} />}
              {running ? "Collecting…" : "Collect all"}
            </button>
          </div>
        )}
      </div>

      {running && (
        <div className="glass rounded-xl p-3">
          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-[#94a3b8] mb-1.5">
            <span>Collecting {status?.current ?? ""}</span>
            <span className="tabular-nums">
              {status?.done ?? 0}/{status?.total ?? 0} · {status?.saved ?? 0} new posts
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 dark:bg-white/5 overflow-hidden">
            <div className="h-full bg-pharma-blue transition-all"
              style={{ width: `${status?.total ? ((status.done ?? 0) / status.total) * 100 : 0}%` }} />
          </div>
        </div>
      )}

      {totals && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: "Accounts tracked", value: totals.accounts },
            { label: "Active", value: totals.active },
            { label: "Producing posts", value: totals.producing },
            { label: "Posts collected", value: totals.posts },
          ].map((stat) => (
            <div key={stat.label} className="glass rounded-xl p-3">
              <p className="text-xl font-bold text-gray-800 dark:text-white tabular-nums">{stat.value}</p>
              <p className="text-[11px] text-gray-400">{stat.label}</p>
            </div>
          ))}
        </div>
      )}

      {adding && isAdmin && <AddAccount onDone={() => {
        setAdding(false);
        qc.invalidateQueries({ queryKey: ["accounts"] });
      }} />}

      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search accounts"
            className="pl-8 pr-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
        </div>
        {["all", ...Object.keys(PLATFORM_META)].map((value) => (
          <button key={value} onClick={() => setPlatform(value)}
            className={cn("px-2.5 py-1.5 text-xs rounded-lg border transition-colors",
              platform === value
                ? "bg-pharma-blue text-white border-pharma-blue"
                : "border-slate-200 dark:border-white/10 text-gray-500 hover:bg-slate-50 dark:hover:bg-white/5")}>
            {value === "all" ? "All" : PLATFORM_META[value].label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" /> Loading accounts…
        </p>
      ) : !accounts.length ? (
        <p className="text-sm text-gray-400">No accounts match.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {accounts.map((account) => (
            <AccountCard key={account.id} account={account} isAdmin={isAdmin}
              onOpen={() => { setEditOnOpen(false); setOpenId(account.id); }}
              onEdit={() => { setEditOnOpen(true); setOpenId(account.id); }}
              onRefresh={() => refreshMut.mutate(account.id)}
              onToggle={() => toggleMut.mutate({ id: account.id, active: !account.active })}
              onDelete={() => deleteMut.mutate(account.id)}
              refreshing={refreshMut.isPending} />
          ))}
        </div>
      )}

      {openId !== null && (
        <AccountDetailPanel id={openId} startEditing={editOnOpen}
          onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}

/** One tracked account, in the card shape used on Social Trends.
 *
 *  The yield badge is the point of the card: an account showing 0 is not
 *  decoration, it is the only signal that a handle is wrong — measured on this
 *  data, every French Facebook slug was wrong and silently produced nothing. */
function AccountCard({ account, isAdmin, onOpen, onEdit, onRefresh, onToggle, onDelete, refreshing }: {
  account: TrackedAccountFull; isAdmin: boolean;
  onOpen: () => void; onEdit: () => void; onRefresh: () => void;
  onToggle: () => void; onDelete: () => void;
  refreshing: boolean;
}) {
  const meta = PLATFORM_META[account.platform];
  const Icon = meta?.icon ?? AtSign;
  const produces = account.post_count > 0;
  const analysed = !!account.analysis?.summary;

  return (
    <div onClick={onOpen}
      className={cn(
        "glass-panel rounded-xl overflow-hidden cursor-pointer hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 flex flex-col",
        !account.active && "opacity-60")}>
      <div className="p-3 flex-1 flex flex-col">
        <div className="flex items-start gap-2 mb-2">
          <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium capitalize flex items-center gap-1",
            PLATFORM_TINT[account.platform] ?? "bg-gray-100 text-gray-600")}>
            <Icon size={10} /> {meta?.label ?? account.platform}
          </span>
          {!account.active && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-white/5 text-gray-500">
              paused
            </span>
          )}
          <span className={cn("ml-auto text-xs tabular-nums px-2 py-0.5 rounded font-medium",
            produces
              ? "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10"
              : "text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10")}
            title={produces ? `${account.post_count} posts collected`
                            : "Nothing collected yet — check the handle is exactly right"}>
            {account.post_count}
          </span>
        </div>

        <p className="text-sm font-semibold text-gray-800 dark:text-[#e2e8f0] truncate">
          {account.label || account.handle}
        </p>
        <p className="text-[11px] text-gray-400 truncate">@{account.handle}</p>

        {account.role && (
          <span className="mt-1.5 self-start text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-gray-500">
            {ROLE_LABELS[account.role] ?? account.role}
          </span>
        )}

        {analysed ? (
          <p className="text-xs text-gray-600 dark:text-[#94a3b8] line-clamp-2 mt-2 flex-1">
            {account.analysis!.summary}
          </p>
        ) : (
          <p className="text-[11px] text-gray-400 mt-2 flex-1">
            {produces ? "Not analysed yet — open to read what they post about."
                      : "No posts collected yet."}
          </p>
        )}

        <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-100 dark:border-slate-800">
          <span className="text-[10px] text-gray-400 truncate">
            checked {relative(account.last_scanned_at)}
            {account.last_scan_status === "empty" && " · found nothing"}
          </span>
          {isAdmin && (
            <span className="ml-auto flex items-center gap-0.5 shrink-0"
              onClick={(e) => e.stopPropagation()}>
              <button onClick={onRefresh} disabled={refreshing} title="Collect this account now"
                className="p-1 text-gray-400 hover:text-pharma-blue disabled:opacity-40">
                <RefreshCw size={12} />
              </button>
              <button onClick={onEdit} title="Edit handle and details"
                className="p-1 text-gray-400 hover:text-pharma-blue">
                <Pencil size={12} />
              </button>
              <button onClick={onToggle}
                title={account.active ? "Pause — keep it, stop collecting" : "Resume"}
                className="p-1 text-gray-400 hover:text-pharma-blue">
                {account.active ? <X size={12} /> : <Check size={12} />}
              </button>
              <button onClick={onDelete} title="Remove. Posts already collected are kept."
                className="p-1 text-gray-400 hover:text-red-500">
                <Trash2 size={12} />
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
