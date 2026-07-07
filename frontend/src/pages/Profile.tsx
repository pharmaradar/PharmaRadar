import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { User as UserIcon, Mail, KeyRound, Check, AlertCircle, Loader2, ShieldCheck, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

export default function Profile() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [detailsMsg, setDetailsMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showCur, setShowCur] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showConf, setShowConf] = useState(false);

  const clean = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+:\s*/, "") : "Something went wrong");

  const detailsMut = useMutation({
    mutationFn: () => api.auth.updateProfile({ name: name.trim(), email: email.trim() }),
    onSuccess: (u) => { setUser(u); setDetailsMsg({ ok: true, text: "Profile updated" }); },
    onError: (e) => setDetailsMsg({ ok: false, text: clean(e) }),
  });

  const pwMut = useMutation({
    mutationFn: () => api.auth.updateProfile({ current_password: current, new_password: next }),
    onSuccess: () => { setPwMsg({ ok: true, text: "Password changed" }); setCurrent(""); setNext(""); setConfirm(""); },
    onError: (e) => setPwMsg({ ok: false, text: clean(e) }),
  });

  const submitPw = (e: React.FormEvent) => {
    e.preventDefault();
    setPwMsg(null);
    if (next.length < 8) return setPwMsg({ ok: false, text: "New password must be at least 8 characters" });
    if (next !== confirm) return setPwMsg({ ok: false, text: "Passwords don't match" });
    pwMut.mutate();
  };

  const Banner = ({ m }: { m: { ok: boolean; text: string } | null }) =>
    m ? (
      <div className={cn("flex items-center gap-2 text-xs rounded-lg px-3 py-2 mb-3 border",
        m.ok ? "text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800"
             : "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800")}>
        {m.ok ? <Check size={13} className="shrink-0" /> : <AlertCircle size={13} className="shrink-0" />}
        <span>{m.text}</span>
      </div>
    ) : null;

  const input = "w-full px-3 py-2.5 rounded-lg border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-sm focus:outline-none focus:ring-2 focus:ring-pharma-blue/40";

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">My profile</h1>
        <p className="text-sm text-gray-500 dark:text-[#94a3b8] mt-1">Manage your account details and password.</p>
      </div>

      {/* Identity */}
      <div className="glass-panel rounded-xl p-5 flex items-center gap-4">
        <div className="w-12 h-12 rounded-full bg-pharma-blue/15 text-pharma-blue dark:text-blue-300 flex items-center justify-center text-lg font-bold uppercase shrink-0">
          {user?.email.slice(0, 1)}
        </div>
        <div className="min-w-0">
          <div className="font-semibold text-slate-900 dark:text-white truncate">{user?.name || user?.email}</div>
          <div className="flex items-center gap-1.5 text-xs mt-0.5 text-slate-500 capitalize">
            {user?.role === "admin" ? <ShieldCheck size={12} className="text-pharma-blue" /> : <UserIcon size={12} />}
            {user?.role}{user?.name && <span className="text-slate-400 normal-case">· {user.email}</span>}
          </div>
        </div>
      </div>

      {/* Name + email */}
      <div className="glass-panel rounded-xl p-5">
        <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><Mail size={15} /> Account details</h2>
        <Banner m={detailsMsg} />
        <form onSubmit={(e) => { e.preventDefault(); setDetailsMsg(null); detailsMut.mutate(); }} className="space-y-3 max-w-sm">
          <div>
            <label className="block text-xs font-semibold text-slate-500 mb-1.5">Full name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} className={input} placeholder="Your name" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 mb-1.5">Email</label>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={input} />
          </div>
          <button type="submit"
            disabled={detailsMut.isPending || (name.trim() === (user?.name ?? "") && email.trim() === user?.email)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-pharma-blue hover:bg-pharma-light text-white text-sm font-semibold disabled:opacity-50">
            {detailsMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save changes
          </button>
        </form>
      </div>

      {/* Password */}
      <div className="glass-panel rounded-xl p-5">
        <h2 className="text-sm font-semibold mb-3 flex items-center gap-2"><KeyRound size={15} /> Change password</h2>
        <Banner m={pwMsg} />
        <form onSubmit={submitPw} className="space-y-3 max-w-sm">
          <div className="relative">
            <input type={showCur ? "text" : "password"} required placeholder="Current password" value={current}
              onChange={(e) => setCurrent(e.target.value)} className={cn(input, "pr-11")} />
            <button type="button" onClick={() => setShowCur((v) => !v)}
              aria-label={showCur ? "Hide password" : "Show password"}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
              {showCur ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="relative">
            <input type={showNew ? "text" : "password"} required placeholder="New password (min 8)" value={next}
              onChange={(e) => setNext(e.target.value)} className={cn(input, "pr-11")} />
            <button type="button" onClick={() => setShowNew((v) => !v)}
              aria-label={showNew ? "Hide password" : "Show password"}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
              {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="relative">
            <input type={showConf ? "text" : "password"} required placeholder="Confirm new password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className={cn(input, "pr-11", confirm && next !== confirm && "border-red-300 dark:border-red-700 focus:ring-red-400/30")} />
            <button type="button" onClick={() => setShowConf((v) => !v)}
              aria-label={showConf ? "Hide password" : "Show password"}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
              {showConf ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <button type="submit" disabled={pwMut.isPending}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-pharma-blue hover:bg-pharma-light text-white text-sm font-semibold disabled:opacity-50">
            {pwMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Update password
          </button>
        </form>
      </div>
    </div>
  );
}
