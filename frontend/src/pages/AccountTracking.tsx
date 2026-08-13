import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, AtSign, Check, ExternalLink, Eye, Facebook, Heart,
  Instagram, Linkedin, Loader2, MessageCircle, Plus, RefreshCw, Search,
  Trash2, Twitter, X,
} from "lucide-react";
import {
  api, type AccountPost, type TrackedAccountFull,
} from "@/lib/api";
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

const STATUS_STYLE: Record<string, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  empty: "text-amber-600 dark:text-amber-400",
  error: "text-red-500",
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

function PostRow({ post }: { post: AccountPost }) {
  // posted_at is null on LinkedIn and X — their search results carry no
  // publication date. Showing collected_at as if it were the post date would
  // misstate when the account was active, so the label changes with the fact.
  const stamp = post.posted_at
    ? `posted ${new Date(post.posted_at).toLocaleDateString()}`
    : `collected ${post.collected_at ? new Date(post.collected_at).toLocaleDateString() : "—"}`;
  const engagement = post.likes + post.comments + post.views;

  return (
    <div className="p-3 rounded-lg border border-slate-200/60 dark:border-white/5 bg-white/50 dark:bg-white/[0.02]">
      <p className="text-sm text-gray-700 dark:text-[#e2e8f0] leading-relaxed">
        {post.text || <span className="italic text-gray-400">No text captured</span>}
      </p>
      <div className="flex items-center gap-3 mt-2 flex-wrap text-[11px] text-gray-400">
        <span>{stamp}</span>
        {engagement > 0 && (
          <>
            {post.likes > 0 && <span className="flex items-center gap-1"><Heart size={10} />{post.likes}</span>}
            {post.comments > 0 && <span className="flex items-center gap-1"><MessageCircle size={10} />{post.comments}</span>}
            {post.views > 0 && <span className="flex items-center gap-1"><Eye size={10} />{post.views}</span>}
          </>
        )}
        {post.language && <span className="uppercase">{post.language}</span>}
        <a href={post.url} target="_blank" rel="noreferrer"
          className="ml-auto text-gray-400 hover:text-pharma-blue" title="Open post">
          <ExternalLink size={11} />
        </a>
      </div>
    </div>
  );
}

function AccountDetailPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [days, setDays] = useState(90);

  const { data, isLoading } = useQuery({
    queryKey: ["account-detail", id, days],
    queryFn: () => api.accounts.detail(id, days),
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

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-2xl h-full overflow-y-auto overlay-panel border-l p-6 space-y-5">
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
          <div className="space-y-2">
            {data.posts.map((post) => <PostRow key={post.id} post={post} />)}
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
        <div className="glass rounded-xl divide-y divide-slate-200/50 dark:divide-white/5">
          {accounts.map((account) => {
            const meta = PLATFORM_META[account.platform];
            const Icon = meta?.icon ?? AtSign;
            return (
              <div key={account.id}
                className={cn("flex items-center gap-3 p-3 hover:bg-slate-50/60 dark:hover:bg-white/[0.02] transition-colors",
                  !account.active && "opacity-50")}>
                <Icon size={15} className={cn("shrink-0", meta?.tint)} />

                <button onClick={() => setOpenId(account.id)} className="min-w-0 flex-1 text-left">
                  <p className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0] truncate">
                    {account.label || account.handle}
                    {account.role && (
                      <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/5 text-gray-500">
                        {ROLE_LABELS[account.role] ?? account.role}
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-gray-400 truncate">
                    @{account.handle} · last checked {relative(account.last_scanned_at)}
                    {account.last_scan_status && (
                      <span className={cn(" · ", STATUS_STYLE[account.last_scan_status])}>
                        {account.last_scan_status === "empty" ? "found nothing" : account.last_scan_status}
                      </span>
                    )}
                  </p>
                </button>

                <div className="flex items-center gap-1.5 shrink-0">
                  <span className={cn("text-xs tabular-nums px-2 py-0.5 rounded-lg",
                    account.post_count > 0
                      ? "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10"
                      : "text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10")}
                    title={account.post_count > 0
                      ? `${account.post_count} posts collected`
                      : "Nothing collected yet — check the handle is exactly right"}>
                    {account.post_count}
                  </span>
                  {isAdmin && (
                    <>
                      <button onClick={() => refreshMut.mutate(account.id)}
                        disabled={refreshMut.isPending}
                        title="Collect this account now"
                        className="p-1.5 text-gray-400 hover:text-pharma-blue disabled:opacity-40">
                        <RefreshCw size={13} />
                      </button>
                      <button onClick={() => toggleMut.mutate({ id: account.id, active: !account.active })}
                        title={account.active ? "Pause — keep it, stop collecting" : "Resume"}
                        className="p-1.5 text-gray-400 hover:text-pharma-blue">
                        {account.active ? <X size={13} /> : <Check size={13} />}
                      </button>
                      <button onClick={() => deleteMut.mutate(account.id)}
                        title="Remove. Posts already collected are kept."
                        className="p-1.5 text-gray-400 hover:text-red-500">
                        <Trash2 size={13} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {openId !== null && (
        <AccountDetailPanel id={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}
