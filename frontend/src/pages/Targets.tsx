import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Building2, ExternalLink, Pencil, Plus, Trash2, User } from "lucide-react";
import { api, type Target } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

const EMPTY_FORM = {
  name: "", known_urls: "", notes: "", twitter_handle: "", linkedin_url: "",
  target_type: "kol" as "kol" | "competitor",
};

function linkLabel(url: string): string {
  try {
    const u = new URL(url.startsWith("http") ? url : `https://${url}`);
    return u.hostname.replace(/^www\./, "") + (u.pathname !== "/" ? u.pathname.slice(0, 18) : "");
  } catch {
    return url.slice(0, 30);
  }
}

/** Every link we know for a target: known_urls + twitter handle + linkedin. */
function allLinks(t: Target): { label: string; href: string }[] {
  const links: { label: string; href: string }[] = [];
  for (const u of t.known_urls || []) {
    if (u.trim()) links.push({ label: linkLabel(u), href: u.startsWith("http") ? u : `https://${u}` });
  }
  if (t.twitter_handle) {
    const handle = t.twitter_handle.replace(/^@/, "");
    links.push({ label: `x.com/${handle}`, href: `https://x.com/${handle}` });
  }
  if (t.linkedin_url) {
    links.push({ label: linkLabel(t.linkedin_url), href: t.linkedin_url.startsWith("http") ? t.linkedin_url : `https://${t.linkedin_url}` });
  }
  // Dedupe by href
  const seen = new Set<string>();
  return links.filter(l => !seen.has(l.href) && seen.add(l.href));
}

export default function Targets() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const { data: targets, isLoading } = useQuery({ queryKey: ["targets"], queryFn: api.targets.list });
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [typeFilter, setTypeFilter] = useState<"all" | "kol" | "competitor">("all");
  const [searchParams, setSearchParams] = useSearchParams();

  // Emerging-voices "Add as KOL/Competitor" lands here with ?add=<name>&type=<type>
  useEffect(() => {
    const add = searchParams.get("add");
    if (add) {
      const type = searchParams.get("type") === "competitor" ? "competitor" : "kol";
      setEditingId(null);
      setForm({ ...EMPTY_FORM, name: add, target_type: type });
      setShowForm(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const closeForm = () => { setShowForm(false); setEditingId(null); setForm(EMPTY_FORM); };

  const openEdit = (t: Target) => {
    setEditingId(t.id);
    setForm({
      name: t.name,
      known_urls: (t.known_urls || []).join("\n"),
      notes: t.notes || "",
      twitter_handle: t.twitter_handle || "",
      linkedin_url: t.linkedin_url || "",
      target_type: t.target_type || "kol",
    });
    setShowForm(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveMut = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name,
        known_urls: form.known_urls.split("\n").map((u) => u.trim()).filter(Boolean),
        notes: form.notes || null,
        target_type: form.target_type,
        twitter_handle: form.twitter_handle.trim() || null,
        linkedin_url: form.linkedin_url.trim() || null,
      };
      return editingId ? api.targets.update(editingId, body) : api.targets.create(body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); closeForm(); },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) => api.targets.update(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["targets"] }),
  });

  const removeMut = useMutation({
    mutationFn: (id: number) => api.targets.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["targets"] }),
  });

  const bulkToggle = async (active: boolean) => {
    if (!targets) return;
    await Promise.all(targets.map(t => api.targets.update(t.id, { active })));
    qc.invalidateQueries({ queryKey: ["targets"] });
  };

  const visible = useMemo(
    () => (targets ?? []).filter(t => typeFilter === "all" || (t.target_type || "kol") === typeFilter),
    [targets, typeFilter],
  );
  const kolCount = (targets ?? []).filter(t => (t.target_type || "kol") === "kol").length;
  const compCount = (targets ?? []).length - kolCount;

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
              onClick={() => { closeForm(); setShowForm(true); }}
              className="flex items-center gap-2 px-4 py-2 bg-pharma-blue text-white rounded-lg text-sm font-medium hover:bg-pharma-light"
            >
              <Plus size={16} /> Add Target
            </button>
          </div>
        )}
      </div>

      {/* Type filter */}
      <div className="inline-flex rounded-lg border border-slate-200 dark:border-[#1e3a5f] p-0.5 -mt-2">
        {([["all", `All (${(targets ?? []).length})`], ["kol", `KOLs (${kolCount})`], ["competitor", `Competitors (${compCount})`]] as const).map(([v, label]) => (
          <button key={v} onClick={() => setTypeFilter(v)}
            className={cn("px-3 py-1.5 text-xs rounded-md transition-colors",
              typeFilter === v ? "bg-pharma-blue text-white" : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-200")}>
            {label}
          </button>
        ))}
      </div>

      {showForm && (
        <div className="glass-panel rounded-xl p-5 shadow-sm border border-slate-200/50 dark:border-white/10">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold">{editingId ? "Edit Target" : "New Target"}</h2>
            <div className="inline-flex rounded-lg border border-slate-200 dark:border-[#1e3a5f] p-0.5">
              <button type="button" onClick={() => setForm(f => ({ ...f, target_type: "kol" }))}
                className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md",
                  form.target_type === "kol" ? "bg-pharma-blue text-white" : "text-slate-500")}>
                <User size={12} /> KOL
              </button>
              <button type="button" onClick={() => setForm(f => ({ ...f, target_type: "competitor" }))}
                className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md",
                  form.target_type === "competitor" ? "bg-orange-500 text-white" : "text-slate-500")}>
                <Building2 size={12} /> Competitor
              </button>
            </div>
          </div>
          <div className="grid gap-3">
            <input
              placeholder={form.target_type === "competitor" ? "Company / account name (e.g. AstraZeneca France)" : "Full name (e.g. Jean Dupont)"}
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
            {saveMut.isError && (
              <div className="text-xs text-red-500">{(saveMut.error as Error)?.message}</div>
            )}
            <div className="flex gap-2 justify-end">
              <button onClick={closeForm} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">Cancel</button>
              <button
                onClick={() => saveMut.mutate()}
                disabled={!form.name || saveMut.isPending}
                className="px-4 py-1.5 bg-pharma-blue text-white rounded-lg text-sm disabled:opacity-50"
              >{editingId ? "Save changes" : "Save"}</button>
            </div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-slate-400">Loading...</div>
      ) : (
        <div className="glass rounded-xl shadow-sm border border-slate-200/50 dark:border-white/10 overflow-hidden">
          <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="border-b border-gray-100 dark:border-[#1e3a5f] text-left text-xs text-gray-500 uppercase tracking-wider">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Links</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 w-32"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50 dark:divide-[#1e3a5f]/50">
              {visible.map((t) => {
                const links = allLinks(t);
                return (
                <tr key={t.id} className="hover:bg-gray-50 dark:hover:bg-[#1e2d4a]">
                  <td className="px-4 py-3 font-medium">{t.name}</td>
                  <td className="px-4 py-3">
                    {(t.target_type || "kol") === "competitor" ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-orange-50 text-orange-600 dark:bg-orange-900/20 dark:text-orange-400">
                        <Building2 size={11} /> Competitor
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400">
                        <User size={11} /> KOL
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 max-w-sm">
                    {links.length === 0 ? (
                      <span className="text-gray-300">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {links.map((l) => (
                          <a key={l.href} href={l.href} target="_blank" rel="noreferrer"
                             className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] bg-slate-100 dark:bg-white/5 text-slate-600 dark:text-slate-300 hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-blue-900/20 transition-colors max-w-[180px]">
                            <span className="truncate">{l.label}</span>
                            <ExternalLink size={9} className="shrink-0" />
                          </a>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${t.active ? "bg-green-50 text-green-600" : "bg-gray-100 text-gray-500"}`}>
                      {t.active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {isAdmin && (
                      <div className="flex items-center gap-1.5 justify-end">
                        <button
                          type="button"
                          onClick={() => toggleMut.mutate({ id: t.id, active: !t.active })}
                          disabled={toggleMut.isPending}
                          title={t.active ? "Disable" : "Enable"}
                          className={cn(
                            "relative w-10 h-5 rounded-full transition-colors focus:outline-none disabled:opacity-50 shrink-0",
                            t.active ? "bg-pharma-light" : "bg-gray-200 dark:bg-[#1e3a5f]"
                          )}
                        >
                          <span className={cn(
                            "block w-4 h-4 rounded-full bg-white shadow transition-transform absolute top-0.5",
                            t.active ? "translate-x-5" : "translate-x-0.5"
                          )} />
                        </button>
                        <button onClick={() => openEdit(t)} title="Edit target"
                          className="p-1.5 text-slate-400 hover:text-pharma-blue">
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Permanently remove "${t.name}" AND all its scraped posts, insights and summaries? (Use the toggle to just deactivate.)`))
                              removeMut.mutate(t.id);
                          }}
                          disabled={removeMut.isPending}
                          title="Remove permanently (posts + insights too)"
                          className="p-1.5 text-slate-400 hover:text-red-500 disabled:opacity-50">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );})}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
