import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

export default function Targets() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const { data: targets, isLoading } = useQuery({ queryKey: ["targets"], queryFn: api.targets.list });
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: "", known_urls: "", notes: "", twitter_handle: "", linkedin_url: "" });

  const createMut = useMutation({
    mutationFn: () => api.targets.create({
      name: form.name,
      known_urls: form.known_urls.split("\n").map((u) => u.trim()).filter(Boolean),
      notes: form.notes || null,
      twitter_handle: form.twitter_handle.trim() || null,
      linkedin_url: form.linkedin_url.trim() || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); setShowAdd(false); setForm({ name: "", known_urls: "", notes: "", twitter_handle: "", linkedin_url: "" }); },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) => api.targets.update(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["targets"] }),
  });

  const bulkToggle = async (active: boolean) => {
    if (!targets) return;
    await Promise.all(targets.map(t => api.targets.update(t.id, { active })));
    qc.invalidateQueries({ queryKey: ["targets"] });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Targets</h1>
        {isAdmin && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => bulkToggle(true)}
              className="px-3 py-1.5 text-xs border border-green-300 dark:border-green-800 text-green-600 dark:text-green-400 rounded-lg hover:bg-green-50 dark:hover:bg-green-900/20 transition-colors"
            >
              Activate All
            </button>
            <button
              onClick={() => bulkToggle(false)}
              className="px-3 py-1.5 text-xs border border-gray-200 dark:border-[#1e3a5f] text-gray-500 dark:text-[#94a3b8] rounded-lg hover:bg-gray-50 dark:hover:bg-[#1e3a5f]/30 transition-colors"
            >
              Deactivate All
            </button>
            <button
              onClick={() => setShowAdd(true)}
              className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light"
            >
              <Plus size={16} /> Add Target
            </button>
          </div>
        )}
      </div>

      {showAdd && (
        <div className="glass-panel rounded-xl p-5 shadow-sm border border-slate-200/50 dark:border-white/10">
          <h2 className="font-semibold mb-4">New Target</h2>
          <div className="grid gap-3">
            <input
              placeholder="Full name (e.g. Jean Dupont)"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
            />
            <textarea
              placeholder="Known URLs (one per line)"
              value={form.known_urls}
              onChange={(e) => setForm((f) => ({ ...f, known_urls: e.target.value }))}
              rows={2}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent resize-none"
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                placeholder="X/Twitter handle (e.g. @DrSmith)"
                value={form.twitter_handle}
                onChange={(e) => setForm((f) => ({ ...f, twitter_handle: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
              <input
                placeholder="LinkedIn URL (optional)"
                value={form.linkedin_url}
                onChange={(e) => setForm((f) => ({ ...f, linkedin_url: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
              />
            </div>
            <input
              placeholder="Notes (optional)"
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-sm bg-transparent"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">Cancel</button>
              <button
                onClick={() => createMut.mutate()}
                disabled={!form.name || createMut.isPending}
                className="px-4 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50"
              >Save</button>
            </div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-slate-400">Loading...</div>
      ) : (
        <div className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden">
          <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[600px]">
            <thead>
              <tr className="border-b border-gray-100 dark:border-[#1e3a5f] text-left text-xs text-gray-500 uppercase tracking-wider">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">X / LinkedIn</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50 dark:divide-[#1e3a5f]/50">
              {targets?.map((t) => (
                <tr key={t.id} className="hover:bg-gray-50 dark:hover:bg-[#1e2d4a]">
                  <td className="px-4 py-3 font-medium">{t.name}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs max-w-xs">
                    <div className="flex flex-col gap-0.5">
                      {t.twitter_handle && <span className="truncate">𝕏 {t.twitter_handle}</span>}
                      {t.linkedin_url && <span className="truncate text-blue-500">in</span>}
                      {!t.twitter_handle && !t.linkedin_url && <span className="text-gray-300">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${t.active ? "bg-green-50 text-green-600" : "bg-gray-100 text-gray-500"}`}>
                      {t.active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={() => toggleMut.mutate({ id: t.id, active: !t.active })}
                        disabled={toggleMut.isPending}
                        title={t.active ? "Disable" : "Enable"}
                        className={cn(
                          "relative w-10 h-5 rounded-full transition-colors focus:outline-none disabled:opacity-50",
                          t.active ? "bg-pharma-light" : "bg-gray-200 dark:bg-[#1e3a5f]"
                        )}
                      >
                        <span className={cn(
                          "block w-4 h-4 rounded-full bg-white shadow transition-transform absolute top-0.5",
                          t.active ? "translate-x-5" : "translate-x-0.5"
                        )} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
