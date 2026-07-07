import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  UserPlus, Trash2, ShieldCheck, User as UserIcon, Loader2, AlertCircle, Check,
  KeyRound, ChevronDown, ChevronRight, Search,
} from "lucide-react";
import { api, type AuthUserDTO } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

type UpdateBody = { name?: string; email?: string; role?: string; is_active?: boolean; password?: string };

export default function Users() {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user);
  const { data: users, isLoading } = useQuery({ queryKey: ["users"], queryFn: api.auth.listUsers });

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["users"] });
  const cleanErr = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+:\s*/, "") : "Something went wrong");
  const createField = "w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-sm focus:outline-none focus:ring-2 focus:ring-pharma-blue/30";

  const createMut = useMutation({
    mutationFn: () => api.auth.createUser(name.trim(), email.trim(), password, role),
    onSuccess: () => { setName(""); setEmail(""); setPassword(""); setConfirmPassword(""); setRole("user"); setError(null); invalidate(); },
    onError: (e) => setError(cleanErr(e)),
  });

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 8) return setError("Password must be at least 8 characters");
    if (password !== confirmPassword) return setError("Passwords don't match");
    setError(null);
    createMut.mutate();
  };
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdateBody }) => api.auth.updateUser(id, body),
    onSuccess: invalidate,
    onError: (e) => alert(cleanErr(e)),
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.auth.deleteUser(id),
    onSuccess: invalidate,
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = users ?? [];
    if (!q) return list;
    return list.filter((u) =>
      (u.name ?? "").toLowerCase().includes(q) ||
      u.email.toLowerCase().includes(q) ||
      u.role.toLowerCase().includes(q)
    );
  }, [users, query]);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Users</h1>
        <p className="text-sm text-gray-500 dark:text-[#94a3b8] mt-1">
          Manage who can access PharmaRadar. Admins have full control; users get view + research access.
        </p>
      </div>

      {/* Create user */}
      <div className="glass-panel rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-3.5 border-b border-slate-200/60 dark:border-white/10">
          <div className="w-7 h-7 rounded-lg bg-pharma-blue/15 text-pharma-blue dark:text-blue-300 flex items-center justify-center shrink-0">
            <UserPlus size={15} />
          </div>
          <div>
            <h2 className="text-sm font-semibold leading-tight">Add a user</h2>
            <p className="text-[11px] text-slate-400">They'll sign in with this email and password.</p>
          </div>
        </div>
        <form onSubmit={submitCreate} className="p-5 space-y-4">
          {error && (
            <div className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2">
              <AlertCircle size={13} className="shrink-0 mt-0.5" /> <span>{error}</span>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
            <div className="md:col-span-5">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Full name</label>
              <input type="text" placeholder="Jane Doe" value={name} onChange={(e) => setName(e.target.value)} className={createField} />
            </div>
            <div className="md:col-span-4">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Email</label>
              <input type="email" required placeholder="email@pharmaradar.com" value={email} onChange={(e) => setEmail(e.target.value)} className={createField} />
            </div>
            <div className="md:col-span-3">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value)} className={createField}>
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <div className="md:col-span-6">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Temp password</label>
              <input type="text" required placeholder="min 8 characters" value={password} onChange={(e) => setPassword(e.target.value)} className={createField} />
            </div>
            <div className="md:col-span-6">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Confirm password</label>
              <input type="text" required placeholder="re-enter password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
                className={cn(createField, confirmPassword && password !== confirmPassword && "border-red-300 dark:border-red-700 focus:ring-red-400/30")} />
            </div>
          </div>
          <div className="flex justify-end">
            <button type="submit" disabled={createMut.isPending}
              className="flex items-center justify-center gap-1.5 px-5 py-2 rounded-lg bg-pharma-blue hover:bg-pharma-light text-white text-sm font-semibold disabled:opacity-50">
              {createMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />} Create user
            </button>
          </div>
        </form>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, email or role…"
          className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-sm focus:outline-none focus:ring-2 focus:ring-pharma-blue/40" />
      </div>

      {/* User list */}
      <div className="glass-panel rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200/60 dark:border-white/10">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Team members</span>
          <span className="text-[11px] text-slate-400 tabular-nums">
            {query ? `${filtered.length} of ${users?.length ?? 0}` : (users?.length ?? 0)}
          </span>
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-gray-400 p-6"><Loader2 size={16} className="animate-spin" /> Loading users…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center text-sm text-slate-400 py-10">No users match "{query}".</div>
        ) : (
          <div className="divide-y divide-slate-100/70 dark:divide-white/5">
            {filtered.map((u) => (
              <UserRow key={u.id} u={u} self={u.id === me?.id}
                busy={updateMut.isPending || deleteMut.isPending}
                onUpdate={(body) => updateMut.mutate({ id: u.id, body })}
                onDelete={() => { if (confirm(`Delete ${u.email}? This can't be undone.`)) deleteMut.mutate(u.id); }} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function UserRow({ u, self, onUpdate, onDelete, busy }: {
  u: AuthUserDTO; self: boolean; busy: boolean;
  onUpdate: (body: UpdateBody) => void; onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(u.name ?? "");
  const [email, setEmail] = useState(u.email);
  const [pw, setPw] = useState("");

  useEffect(() => { setName(u.name ?? ""); setEmail(u.email); }, [u.name, u.email]);

  const viewer = useAuthStore((s) => s.user);
  const detailsChanged = name.trim() !== (u.name ?? "") || email.trim() !== u.email;
  const protectedAcct = !!u.is_superadmin;   // super admin: can't be demoted, deactivated, or deleted
  // Only the super admin can touch the super admin account (name/email/password too)
  const locked = protectedAcct && !viewer?.is_superadmin;
  const field = "w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-sm focus:outline-none focus:ring-2 focus:ring-pharma-blue/30 disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <div>
      {/* Collapsed header */}
      <button onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-slate-50/70 dark:hover:bg-white/5 transition-colors">
        {open ? <ChevronDown size={16} className="text-slate-400 shrink-0" /> : <ChevronRight size={16} className="text-slate-400 shrink-0" />}
        <div className={cn("w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold uppercase shrink-0",
          u.is_active ? "bg-pharma-blue/15 text-pharma-blue dark:text-blue-300" : "bg-slate-200 dark:bg-slate-700 text-slate-400")}>
          {(u.name || u.email).slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{u.name || u.email.split("@")[0]}</span>
            {self && <span className="text-[10px] text-slate-400 shrink-0">you</span>}
          </div>
          <div className="text-xs text-slate-400 truncate">{u.email}</div>
        </div>
        {/* Role */}
        <span className={cn("hidden sm:inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold shrink-0",
          u.is_superadmin ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
            : u.role === "admin" ? "bg-pharma-blue/10 text-pharma-blue dark:text-blue-300"
            : "bg-slate-100 dark:bg-slate-800 text-slate-500")}>
          {u.role === "admin" ? <ShieldCheck size={12} /> : <UserIcon size={12} />} {u.is_superadmin ? "super admin" : u.role}
        </span>
        {/* Active status */}
        <span className={cn("inline-flex items-center gap-1.5 text-xs shrink-0 w-[72px]",
          u.is_active ? "text-green-600 dark:text-green-400" : "text-slate-400")}>
          <span className={cn("w-1.5 h-1.5 rounded-full", u.is_active ? "bg-green-500" : "bg-slate-400")} />
          {u.is_active ? "Active" : "Inactive"}
        </span>
      </button>

      {/* Expanded profile panel */}
      {open && (
        <div className="px-5 pb-5 pt-1 ml-[52px] space-y-4">
          {/* Name + email */}
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} className={field} placeholder="Full name" disabled={locked} />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1">Email</label>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} disabled={locked} />
            </div>
          </div>
          {locked ? (
            <p className="text-[10px] text-amber-600 dark:text-amber-400">Only the super admin can edit this account.</p>
          ) : (
            <button disabled={!detailsChanged || busy}
              onClick={() => onUpdate({ name: name.trim(), email: email.trim() })}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-pharma-blue hover:bg-pharma-light text-white text-xs font-semibold disabled:opacity-40">
              <Check size={13} /> Save details
            </button>
          )}

          {/* Role + status */}
          <div className="grid sm:grid-cols-2 gap-4 pt-1">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Role</label>
              <div className="inline-flex p-0.5 rounded-lg bg-slate-100 dark:bg-slate-800/70">
                {(["user", "admin"] as const).map((r) => (
                  <button key={r} disabled={self || busy || protectedAcct}
                    onClick={() => u.role !== r && onUpdate({ role: r })}
                    className={cn("px-3 py-1 rounded-md text-xs font-medium capitalize transition-all",
                      u.role === r ? "bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-sm" : "text-slate-500",
                      (self || protectedAcct) && "opacity-50 cursor-not-allowed")}>
                    {r}
                  </button>
                ))}
              </div>
              {protectedAcct ? <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1">Super admin — role is locked.</p>
                : self && <p className="text-[10px] text-slate-400 mt-1">You can't change your own role.</p>}
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Status</label>
              <button disabled={self || busy || protectedAcct} onClick={() => onUpdate({ is_active: !u.is_active })}
                className={cn("inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                  u.is_active
                    ? "border-amber-300 dark:border-amber-800 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-900/20"
                    : "border-green-300 dark:border-green-800 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20",
                  (self || protectedAcct) && "opacity-50 cursor-not-allowed")}>
                {u.is_active ? "Deactivate account" : "Activate account"}
              </button>
              {protectedAcct && <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1">Super admin can't be deactivated.</p>}
            </div>
          </div>

          {/* Reset password */}
          <div className="pt-1">
            <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Reset password</label>
            <div className="flex gap-2 max-w-md">
              <input type="text" value={pw} onChange={(e) => setPw(e.target.value)}
                placeholder="New password (min 8)" className={field} disabled={locked} />
              <button disabled={pw.length < 8 || busy || locked}
                onClick={() => { onUpdate({ password: pw }); setPw(""); }}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-slate-200 dark:border-[#1e3a5f] text-slate-600 dark:text-slate-300 text-xs font-semibold hover:bg-slate-50 dark:hover:bg-white/5 disabled:opacity-40 shrink-0">
                <KeyRound size={13} /> Set
              </button>
            </div>
          </div>

          {/* Danger zone */}
          {!self && !protectedAcct && (
            <div className="pt-2 border-t border-slate-100 dark:border-white/5">
              <button onClick={onDelete} disabled={busy}
                className="flex items-center gap-1.5 mt-3 px-3 py-1.5 rounded-lg text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-40">
                <Trash2 size={13} /> Delete user
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
