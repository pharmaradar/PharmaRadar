import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, AlertCircle, ArrowRight, Activity, Flame, Sparkles, ShieldCheck, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

const FEATURES = [
  { icon: Activity, title: "KOL monitoring", desc: "Track what 100 key opinion leaders publish, weekly." },
  { icon: Flame, title: "Social & web trends", desc: "Surface emerging signals across platforms in real time." },
  { icon: Sparkles, title: "AI synthesis", desc: "Turn raw activity into a pharma-focused intelligence brief." },
];

export default function Login() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const r = await api.auth.login(email.trim(), password);
      setAuth(r.access_token, r.user);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message.replace(/^\d+:\s*/, "") : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-white dark:bg-[#0a0f1e]">
      {/* ── Brand / landing panel ── */}
      <div className="hidden lg:flex lg:w-1/2 relative overflow-hidden bg-pharma-blue text-white">
        {/* depth + texture */}
        <div className="absolute inset-0 bg-gradient-to-br from-[#0a4fa0] via-pharma-blue to-[#04244e]" />
        <div className="absolute inset-0 dot-grid opacity-50" />
        <div className="absolute -top-32 -right-32 w-[28rem] h-[28rem] rounded-full bg-cyan-400/10 blur-3xl" />

        {/* Radar backdrop — concentric rings + rotating sweep */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[42rem] h-[42rem] pointer-events-none select-none">
          <div className="absolute inset-0 rounded-full border border-white/10" />
          <div className="absolute inset-[12%] rounded-full border border-white/10" />
          <div className="absolute inset-[26%] rounded-full border border-white/[0.07]" />
          <div className="absolute inset-[40%] rounded-full border border-white/[0.06]" />
          <div className="absolute left-0 right-0 top-1/2 h-px bg-white/[0.06]" />
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-white/[0.06]" />
          <div className="absolute inset-0 rounded-full overflow-hidden">
            <div className="radar-sweep absolute inset-0 origin-center" />
          </div>
        </div>

        <div className="relative z-10 flex flex-col p-12 xl:p-16 w-full">
          {/* logo pinned tight to the top-left corner */}
          <div className="absolute top-7 left-8 xl:left-10 flex items-center gap-3 fade-up">
            <div className="relative w-11 h-11 rounded-xl bg-white/15 backdrop-blur flex items-center justify-center text-lg font-bold">
              PR
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5">
                <span className="ping-ring absolute inset-0 rounded-full bg-cyan-300" />
                <span className="absolute inset-0 rounded-full bg-cyan-300" />
              </span>
            </div>
            <span className="text-lg font-semibold tracking-wide">PharmaRadar</span>
          </div>

          {/* Centered hero */}
          <div className="flex-1 flex flex-col items-center justify-center text-center py-10">
            <div className="max-w-xl">
              <span className="fade-up inline-flex items-center gap-2 px-3 py-1 mb-6 rounded-full bg-white/10 border border-white/15 text-[11px] font-medium tracking-wide text-white/80"
                style={{ animationDelay: "0.05s" }}>
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-300" /> Pharma intelligence platform
              </span>
              <h1 className="fade-up text-4xl xl:text-5xl font-bold leading-[1.1] tracking-tight" style={{ animationDelay: "0.1s" }}>
                Intelligence that finds<br />the signal in the noise.
              </h1>
              <p className="fade-up mt-6 text-lg text-white/70 leading-relaxed max-w-lg mx-auto" style={{ animationDelay: "0.18s" }}>
                Monitor the people and conversations shaping your therapeutic areas — distilled into one weekly brief.
              </p>

              <div className="mt-12 space-y-5 text-left max-w-md mx-auto">
                {FEATURES.map(({ icon: Icon, title, desc }, i) => (
                  <div key={title}
                    className="fade-up flex items-start gap-4 rounded-2xl p-3 -mx-3 hover:bg-white/5 transition-colors"
                    style={{ animationDelay: `${0.28 + i * 0.1}s` }}>
                    <div className="w-11 h-11 rounded-xl bg-white/10 border border-white/10 flex items-center justify-center shrink-0">
                      <Icon size={20} className="text-cyan-200" />
                    </div>
                    <div>
                      <p className="text-base font-semibold">{title}</p>
                      <p className="text-sm text-white/55 leading-relaxed">{desc}</p>
                    </div>
                  </div>
                ))}
              </div>

              {/* Stats strip */}
              <div className="fade-up mt-12 flex items-center justify-center divide-x divide-white/15"
                style={{ animationDelay: "0.6s" }}>
                {[["100", "KOLs tracked"], ["4", "platforms"], ["Weekly", "intel brief"]].map(([n, l]) => (
                  <div key={l} className="px-6">
                    <div className="text-2xl font-bold">{n}</div>
                    <div className="text-[11px] uppercase tracking-wider text-white/50 mt-0.5">{l}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-center gap-2 text-xs text-white/50">
            <ShieldCheck size={13} /> Secure access · accounts provisioned by your administrator
          </div>
        </div>
      </div>

      {/* ── Sign-in form ── */}
      <div className="bg-noise flex-1 flex items-center justify-center px-6 sm:px-12 py-12 relative bg-slate-50 dark:bg-[#0a0f1e]">
        {/* dotted texture, softly faded toward the edges */}
        <div className="absolute inset-0 dot-grid-slate pointer-events-none
          [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_72%)]
          [-webkit-mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_72%)]" />

        <div className="relative w-full max-w-[560px] fade-up">
          <div className="rounded-2xl bg-white dark:bg-[#0f1729] border border-slate-200/80 dark:border-white/10 shadow-xl shadow-slate-300/40 dark:shadow-black/40 p-10">
            {/* logo mark + live heartbeat trace */}
            <div className="flex items-center gap-4 mb-7">
              <div className="w-14 h-14 rounded-2xl bg-pharma-blue text-white flex items-center justify-center text-xl font-bold shadow-md shadow-pharma-blue/30 shrink-0">PR</div>
              <svg viewBox="0 0 120 40" className="h-9 w-32 overflow-visible" fill="none" aria-hidden="true">
                {/* faint baseline waveform */}
                <path d="M0,20 L20,20 L24,18 L28,22 L33,5 L38,35 L43,13 L47,20 L70,20 L74,20 L78,16 L82,24 L86,20 L120,20"
                  className="stroke-pharma-blue/15" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                {/* bright sweeping pulse */}
                <path d="M0,20 L20,20 L24,18 L28,22 L33,5 L38,35 L43,13 L47,20 L70,20 L74,20 L78,16 L82,24 L86,20 L120,20"
                  pathLength={100}
                  className="ecg-trace stroke-pharma-blue" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
                  style={{ filter: "drop-shadow(0 0 4px rgba(0,102,204,0.65))" }} />
              </svg>
            </div>

            <h2 className="text-[28px] font-bold text-slate-900 dark:text-white tracking-tight">Welcome back</h2>
            <p className="text-[15px] text-slate-500 dark:text-slate-400 mt-2 mb-8">Sign in to your intelligence dashboard.</p>

            <form onSubmit={submit} className="space-y-5">
              {error && (
                <div className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3.5 py-2.5">
                  <AlertCircle size={15} className="shrink-0 mt-0.5" />
                  <span>{error}</span>
                </div>
              )}
              <div>
                <label className="block text-sm font-semibold text-slate-600 dark:text-slate-300 mb-2">Email</label>
                <input type="email" required autoFocus value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-4 py-3.5 rounded-xl border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-base text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-pharma-blue/40 focus:border-pharma-blue/40 transition"
                  placeholder="you@pharmaradar.com" />
              </div>
              <div>
                <label className="block text-sm font-semibold text-slate-600 dark:text-slate-300 mb-2">Password</label>
                <div className="relative">
                  <input type={showPassword ? "text" : "password"} required value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-4 py-3.5 pr-11 rounded-xl border border-slate-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0f2744] text-base text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-pharma-blue/40 focus:border-pharma-blue/40 transition"
                    placeholder="••••••••" />
                  <button type="button" onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
                    {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>
              <button type="submit" disabled={loading}
                className="w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-xl bg-pharma-blue hover:bg-pharma-light text-white text-base font-semibold transition-all disabled:opacity-50 mt-2 shadow-md shadow-pharma-blue/30 hover:-translate-y-0.5">
                {loading ? <Loader2 size={18} className="animate-spin" /> : <>Sign in <ArrowRight size={18} /></>}
              </button>
            </form>
          </div>

          <p className="text-center text-[13px] text-slate-400 mt-6">
            Trouble signing in? Contact your administrator.
          </p>
        </div>
      </div>
    </div>
  );
}
