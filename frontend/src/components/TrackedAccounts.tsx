import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, Check, Loader2, Plus, Trash2, X } from "lucide-react";
import { api, type TrackedAccount } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

/**
 * The accounts the platform monitors directly.
 *
 * Keyword search pays for N results and hopes some are relevant; a chosen
 * account is on-topic and from a known voice by construction. This is the
 * client's "define and track specific social media accounts", and the highest-
 * yield lever on French volume.
 */

const PLATFORMS = ["twitter", "linkedin", "instagram", "facebook"] as const;

const PLATFORM_LABELS: Record<string, string> = {
  twitter: "X / Twitter",
  linkedin: "LinkedIn",
  instagram: "Instagram",
  facebook: "Facebook",
};

/** Per-platform caveats, shown so nobody adds an account that is silently
 *  never collected. Instagram is now scraped by profile; LinkedIn is reached
 *  through its French locale rather than per-account. */
const PLATFORM_NOTE: Record<string, string> = {
  instagram: "Posts and their comments are collected for these accounts.",
  linkedin: "Collected via fr.linkedin.com search rather than per-account.",
};

export default function TrackedAccounts() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ platform: "twitter", handle: "", label: "" });

  const { data, isLoading } = useQuery({
    queryKey: ["tracked-accounts"],
    queryFn: api.social.accounts,
  });
  const accounts = data?.accounts ?? [];

  const invalidate = () => qc.invalidateQueries({ queryKey: ["tracked-accounts"] });

  const createMut = useMutation({
    mutationFn: () => api.social.createAccount({
      platform: form.platform as TrackedAccount["platform"],
      handle: form.handle.trim(),
      label: form.label.trim() || null,
    }),
    onSuccess: () => {
      setForm({ platform: form.platform, handle: "", label: "" });
      setAdding(false);
      invalidate();
    },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      api.social.updateAccount(id, { active }),
    onSuccess: invalidate,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.social.deleteAccount(id),
    onSuccess: invalidate,
  });

  const byPlatform = PLATFORMS.map((platform) => ({
    platform,
    items: accounts.filter((a) => a.platform === platform),
  })).filter((group) => group.items.length > 0 || adding);

  return (
    <div className="glass rounded-xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="p-2 bg-pharma-blue/10 rounded-lg shrink-0">
            <AtSign size={16} className="text-pharma-blue dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-sm">Tracked accounts</h2>
            <p className="text-xs text-gray-400">
              Every post from these is collected directly — no keyword luck involved.
            </p>
          </div>
        </div>
        {isAdmin && (
          <button onClick={() => setAdding(!adding)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 dark:border-white/10 rounded-lg hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
            {adding ? <X size={12} /> : <Plus size={12} />}
            {adding ? "Cancel" : "Add account"}
          </button>
        )}
      </div>

      {adding && isAdmin && (
        <div className="grid gap-2 sm:grid-cols-[130px_1fr_1fr_auto] items-start p-3 rounded-lg bg-slate-50/60 dark:bg-white/5">
          <select value={form.platform}
            onChange={(e) => setForm((f) => ({ ...f, platform: e.target.value }))}
            className="px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">
            {PLATFORMS.map((p) => (
              <option key={p} value={p} className="dark:bg-[#0d1424]">{PLATFORM_LABELS[p]}</option>
            ))}
          </select>
          <input value={form.handle} placeholder="handle or page slug (e.g. GustaveRoussy)"
            onChange={(e) => setForm((f) => ({ ...f, handle: e.target.value }))}
            className="px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
          <input value={form.label} placeholder="Label (optional)"
            onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
            className="px-2 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" />
          <button onClick={() => createMut.mutate()}
            disabled={!form.handle.trim() || createMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50">
            {createMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            Add
          </button>
          {createMut.isError && (
            <p className="sm:col-span-4 text-xs text-red-400">
              {(createMut.error as Error)?.message?.includes("409")
                ? "That account is already tracked."
                : (createMut.error as Error)?.message}
            </p>
          )}
          {PLATFORM_NOTE[form.platform] && (
            <p className="sm:col-span-4 text-[11px] text-amber-600 dark:text-amber-400">
              {PLATFORM_NOTE[form.platform]}
            </p>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />Loading accounts…
        </p>
      ) : accounts.length === 0 ? (
        <p className="text-sm text-gray-400">No accounts tracked yet.</p>
      ) : (
        <div className="space-y-3">
          {byPlatform.map(({ platform, items }) => (
            <div key={platform}>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                  {PLATFORM_LABELS[platform]}
                </span>
                <span className="text-[10px] text-gray-400">{items.length}</span>
                {PLATFORM_NOTE[platform] && (
                  <span className="text-[10px] text-amber-600 dark:text-amber-400">
                    {PLATFORM_NOTE[platform]}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {items.map((account) => (
                  <span key={account.id}
                    className={cn(
                      "group inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 rounded-lg border text-xs",
                      account.active
                        ? "border-slate-200 dark:border-white/10 text-slate-700 dark:text-slate-200"
                        : "border-slate-200/60 dark:border-white/5 text-slate-400 line-through"
                    )}
                    title={account.label || account.handle}>
                    {account.label || account.handle}
                    {isAdmin && (
                      <>
                        <button
                          onClick={() => toggleMut.mutate({ id: account.id, active: !account.active })}
                          className="text-gray-400 hover:text-pharma-blue"
                          title={account.active ? "Pause — keep it, stop scraping it" : "Resume"}>
                          {account.active ? <X size={11} /> : <Check size={11} />}
                        </button>
                        <button onClick={() => deleteMut.mutate(account.id)}
                          className="text-gray-400 hover:text-red-500" title="Remove">
                          <Trash2 size={11} />
                        </button>
                      </>
                    )}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
