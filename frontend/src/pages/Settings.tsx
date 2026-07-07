import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import {
  Save, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronRight,
  Cpu, Calendar, Gauge, Info, Activity, Square, Trash2,
  Flame, Play, Loader2, AlertTriangle, CircleSlash, ServerCog,
} from "lucide-react";
import { api, type AppSettings, type ProviderHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

function PipelineStatus() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["current-run"],
    queryFn: api.runs.current,
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 30000),
  });
  const stopMut = useMutation({
    mutationFn: api.runs.stop,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["current-run"] }),
  });

  const running = data?.running;
  const status = data?.status;
  const pct = data?.total_targets
    ? Math.round(((data.targets_processed ?? 0) / data.total_targets) * 100)
    : null;

  const dot = running
    ? "bg-green-400 animate-pulse"
    : status === "success" ? "bg-green-500"
    : status === "error" ? "bg-red-500"
    : "bg-gray-300 dark:bg-gray-600";

  const label = running
    ? `Running${data?.current_target ? ` — ${data.current_target}` : ""}${pct !== null ? ` (${pct}%)` : ""}`
    : status === "success" ? "Last run completed successfully"
    : status === "error" ? `Last run failed${data?.error_message ? `: ${data.error_message}` : ""}`
    : "No active pipeline";

  return (
    <div className="flex items-center justify-between px-4 py-3 glass rounded-xl shadow-sm">
      <div className="flex items-center gap-3 min-w-0">
        <Activity size={15} className="text-pharma-light shrink-0" />
        <span className="text-xs font-medium text-gray-500 dark:text-[#94a3b8] shrink-0">Pipeline</span>
        <span className={cn("w-2 h-2 rounded-full shrink-0", dot)} />
        <span className="text-sm text-gray-700 dark:text-[#e2e8f0] truncate">{label}</span>
        {running && pct !== null && (
          <div className="hidden sm:block w-32 h-1.5 bg-gray-100 dark:bg-[#1e3a5f] rounded-full overflow-hidden shrink-0">
            <div className="h-full bg-pharma-light rounded-full transition-all" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
      {running && (
        <button
          onClick={() => stopMut.mutate()}
          disabled={stopMut.isPending}
          className="ml-4 shrink-0 flex items-center gap-1 px-3 py-1 text-xs font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
        >
          <Square size={11} />
          Stop
        </button>
      )}
    </div>
  );
}

function ProviderRow({ p }: { p: ProviderHealth }) {
  const meta = {
    ok:        { color: "text-green-600 dark:text-green-400", dot: "bg-green-500", Icon: CheckCircle },
    low:       { color: "text-amber-600 dark:text-amber-400", dot: "bg-amber-500", Icon: AlertTriangle },
    exhausted: { color: "text-red-600 dark:text-red-400",     dot: "bg-red-500",   Icon: CircleSlash },
    error:     { color: "text-red-600 dark:text-red-400",     dot: "bg-red-500",   Icon: XCircle },
    unknown:   { color: "text-gray-400",                      dot: "bg-gray-300",  Icon: Info },
  }[p.status] ?? { color: "text-gray-400", dot: "bg-gray-300", Icon: Info };
  const StatusIcon = meta.Icon;
  const barPct = p.percent != null ? Math.min(100, p.percent) : null;
  return (
    <div className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-center gap-2">
        <span className={cn("w-2 h-2 rounded-full shrink-0", meta.dot)} />
        <span className="text-sm font-medium text-gray-800 dark:text-[#e2e8f0]">{p.name}</span>
        <StatusIcon size={13} className={cn("shrink-0", meta.color)} />
        {!p.configured && <span className="text-[10px] text-gray-400">not configured</span>}
        {p.usage_label ? (
          <span className="ml-auto text-xs font-mono text-gray-500 dark:text-[#94a3b8]">{p.usage_label}</span>
        ) : p.usage_usd != null && (
          <span className="ml-auto text-xs font-mono text-gray-500 dark:text-[#94a3b8]">
            ${p.usage_usd.toFixed(2)}{p.limit_usd ? ` / $${p.limit_usd.toFixed(2)}` : ""}
          </span>
        )}
      </div>
      {barPct != null && (
        <div className="mt-1.5 h-1.5 bg-gray-100 dark:bg-[#1e3a5f] rounded-full overflow-hidden">
          <div className={cn("h-full rounded-full transition-all",
            p.status === "exhausted" ? "bg-red-500" : p.status === "low" ? "bg-amber-500" : "bg-green-500")}
            style={{ width: `${barPct}%` }} />
        </div>
      )}
      {p.message && (
        <p className={cn("text-xs mt-1", p.status === "exhausted" || p.status === "error" ? meta.color : "text-gray-400 dark:text-[#64748b]")}>
          {p.message}
        </p>
      )}
    </div>
  );
}

function ProviderHealthPanel() {
  const qc = useQueryClient();
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["provider-health"],
    queryFn: () => api.health.providers(false),
    refetchInterval: 120000,
  });
  const refreshMut = useMutation({
    mutationFn: () => api.health.providers(true),
    onSuccess: (d) => qc.setQueryData(["provider-health"], d),
  });
  const providers = data?.providers ?? [];
  const anyBad = providers.some(p => p.status === "exhausted" || p.status === "error");
  const anyLow = providers.some(p => p.status === "low");
  const summary = (
    <span className={cn("flex items-center gap-1.5 text-[11px] font-medium",
      anyBad ? "text-red-600 dark:text-red-400" : anyLow ? "text-amber-600 dark:text-amber-400" : "text-green-600 dark:text-green-400")}>
      <span className={cn("w-2 h-2 rounded-full", anyBad ? "bg-red-500" : anyLow ? "bg-amber-500" : "bg-green-500")} />
      {isLoading ? "checking…" : anyBad ? "action needed" : anyLow ? "running low" : `all ok · ${providers.length}`}
    </span>
  );
  return (
    <Card icon={<ServerCog size={16} />} title="Service health & usage"
      subtitle="API keys, scraper credits and live usage"
      collapsible defaultCollapsed headerRight={summary}>
      {anyBad && (
        <div className="flex items-start gap-2 text-xs text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 mb-1">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <span>A scraping provider is out of credits or unreachable — runs may fail until resolved.</span>
        </div>
      )}
      {isLoading ? (
        <div className="flex items-center gap-2 text-xs text-gray-400 py-2">
          <RefreshCw size={13} className="animate-spin" /> Checking providers…
        </div>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-[#1e3a5f]/50">
          {providers.map(p => <ProviderRow key={p.id} p={p} />)}
        </div>
      )}
      <button onClick={() => refreshMut.mutate()} disabled={isFetching || refreshMut.isPending}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 dark:border-[#1e3a5f] rounded-lg text-gray-600 dark:text-[#94a3b8] hover:bg-gray-50 dark:hover:bg-[#0f2744] disabled:opacity-50 mt-1">
        <RefreshCw size={13} className={cn((isFetching || refreshMut.isPending) && "animate-spin")} /> Refresh now
      </button>
    </Card>
  );
}

const PROVIDER_NOTES: Record<string, string> = {
  vertex:     "GCP service account / ADC. Set GOOGLE_APPLICATION_CREDENTIALS in .env",
  openrouter: "200+ models. Set OPENROUTER_API_KEY in .env",
  ollama:     "Local inference. Make sure Ollama is running.",
  nvidia:     "NVIDIA NIM cloud. Set NVIDIA_API_KEY in .env",
  anthropic:  "Claude models. Set ANTHROPIC_API_KEY in .env",
  openai:     "GPT models. Set OPENAI_API_KEY in .env",
  gemini:     "Gemini via AI Studio. Set GEMINI_API_KEY in .env",
};

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: settings, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });
  const [form, setForm] = useState<Partial<AppSettings>>({});
  const [saveState, setSaveState] = useState<"idle" | "saved" | "error">("idle");
  const [testState, setTestState] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [testError, setTestError] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  useEffect(() => { if (settings) setForm({ ...settings }); }, [settings]);

  const updateMut = useMutation({
    mutationFn: () => api.settings.update(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2500);
    },
    onError: () => { setSaveState("error"); setTimeout(() => setSaveState("idle"), 2500); },
  });

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }));

  async function fetchModels() {
    setModelsLoading(true); setModels([]);
    try { const r = await api.settings.fetchModels({ provider: form.llm_provider }); setModels(r.models); }
    catch { setModels([]); }
    setModelsLoading(false);
  }

  async function testConnection() {
    setTestState("loading"); setTestError("");
    try {
      await api.settings.testConnection({ provider: form.llm_provider, model: form.llm_model });
      setTestState("ok");
    } catch (e: unknown) {
      setTestState("fail");
      setTestError(e instanceof Error ? e.message : String(e));
    }
    setTimeout(() => setTestState("idle"), 6000);
  }

  if (isLoading) return (
    <div className="flex items-center justify-center py-24 text-gray-400">
      <RefreshCw size={20} className="animate-spin mr-2" /> Loading settings…
    </div>
  );

  const provider = form.llm_provider || "gemini";
  const frequency = form.cron_frequency ?? "weekly";

  return (
    <div className="max-w-5xl space-y-6">

      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Settings</h1>
        <p className="text-sm text-gray-500 dark:text-[#94a3b8] mt-1">
          Configure the pipeline, LLM provider, and schedule.
        </p>
      </div>

      <PipelineStatus />

      <ProviderHealthPanel />

      {/* ── 2-column grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* ── LEFT: LLM Provider ── */}
        <Card icon={<Cpu size={16} />} title="LLM Provider" subtitle="Model used for extraction and summaries">

          {/* Key reminder */}
          <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400
            bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/40
            rounded-lg px-3 py-2.5">
            <Info size={13} className="shrink-0 mt-0.5" />
            <span>API keys live in <code className="font-mono">.env</code> — never stored in the database.</span>
          </div>

          {/* Provider select */}
          <Field label="Provider">
            <div className="relative">
              <select
                value={provider}
                onChange={e => { set("llm_provider", e.target.value); setModels([]); setTestState("idle"); }}
                className={input}
              >
                {Object.entries(settings?.available_providers ?? {}).map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
              <ChevronDown size={14} className="absolute right-2.5 top-3 text-gray-400 pointer-events-none" />
            </div>
            {PROVIDER_NOTES[provider] && (
              <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">{PROVIDER_NOTES[provider]}</p>
            )}
          </Field>

          {/* Conditional URL fields */}
          {provider === "ollama" && (
            <Field label="Ollama Base URL">
              <input value={form.ollama_base_url ?? "http://localhost:11434"}
                onChange={e => set("ollama_base_url", e.target.value)} className={`${input} font-mono`} />
            </Field>
          )}
          {provider === "nvidia" && (
            <Field label="NVIDIA NIM Base URL">
              <input value={form.nvidia_base_url ?? "https://integrate.api.nvidia.com/v1"}
                onChange={e => set("nvidia_base_url", e.target.value)} className={`${input} font-mono`} />
            </Field>
          )}
          {provider === "openai" && (
            <Field label="Custom Base URL (optional)">
              <input value={form.custom_base_url ?? ""} placeholder="https://your-endpoint/v1"
                onChange={e => set("custom_base_url", e.target.value)} className={`${input} font-mono`} />
            </Field>
          )}

          {/* Fetch models */}
          <div className="flex items-center gap-3">
            <button onClick={fetchModels} disabled={modelsLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200
                dark:border-[#1e3a5f] rounded-lg text-gray-500 dark:text-[#94a3b8]
                hover:border-pharma-light hover:text-pharma-light transition-colors disabled:opacity-50">
              <RefreshCw size={12} className={modelsLoading ? "animate-spin" : ""} />
              {modelsLoading ? "Fetching…" : "Fetch models"}
            </button>
            {models.length > 0 && (
              <span className="text-xs text-green-500 font-medium">{models.length} available</span>
            )}
          </div>

          <ModelField label="Model" value={form.llm_model ?? ""} onChange={v => set("llm_model", v)} suggestions={models} />

          {/* Test connection */}
          <div className="flex items-center gap-3 pt-1 border-t border-gray-100 dark:border-[#1e3a5f]/50">
            <button onClick={testConnection} disabled={testState === "loading"}
              className={cn(
                "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50",
                testState === "ok"   ? "bg-green-50 dark:bg-green-900/20 text-green-600 border border-green-200 dark:border-green-800/40" :
                testState === "fail" ? "bg-red-50 dark:bg-red-900/20 text-red-600 border border-red-200 dark:border-red-800/40" :
                "border border-pharma-light text-pharma-light hover:bg-pharma-light hover:text-white"
              )}>
              {testState === "loading" ? <><RefreshCw size={13} className="animate-spin" /> Testing…</> :
               testState === "ok"      ? <><CheckCircle size={13} /> Connected</> :
               testState === "fail"    ? <><XCircle size={13} /> Failed</> :
               "Test connection"}
            </button>
            {testError && (
              <p className="text-xs text-red-500 truncate max-w-[200px]" title={testError}>{testError}</p>
            )}
          </div>
        </Card>

        {/* ── RIGHT COLUMN: Schedule + Budget stacked ── */}
        <div className="space-y-6">

          {/* Schedule */}
          <Card icon={<Calendar size={16} />} title="Run Schedule" subtitle="When the pipeline runs automatically">

            {/* Frequency toggle */}
            <Field label="Frequency">
              <div className="grid grid-cols-2 gap-2">
                {(["weekly", "daily"] as const).map(f => (
                  <button key={f} type="button" onClick={() => set("cron_frequency", f)}
                    className={cn(
                      "py-2 rounded-lg text-sm font-medium border transition-colors",
                      frequency === f
                        ? "bg-pharma-blue text-white border-pharma-blue dark:bg-[#2563eb] dark:border-[#2563eb]"
                        : "border-gray-200 dark:border-[#1e3a5f] text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light"
                    )}>
                    {f === "weekly" ? "Weekly" : "Daily"}
                  </button>
                ))}
              </div>
            </Field>

            {/* Day of week */}
            {frequency === "weekly" && (
              <Field label="Day of week">
                <div className="flex gap-1 flex-wrap">
                  {DOW.map((d, i) => (
                    <button key={i} type="button" onClick={() => set("cron_day_of_week", i)}
                      className={cn(
                        "px-2.5 py-1.5 rounded-md text-xs font-semibold border transition-colors",
                        (form.cron_day_of_week ?? 1) === i
                          ? "bg-pharma-blue text-white border-pharma-blue dark:bg-[#2563eb] dark:border-[#2563eb]"
                          : "border-gray-200 dark:border-[#1e3a5f] text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light"
                      )}>
                      {d}
                    </button>
                  ))}
                </div>
              </Field>
            )}

            {/* Time — side by side */}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Hour (0–23)">
                <input type="number" min={0} max={23} value={form.cron_hour ?? 8}
                  onChange={e => set("cron_hour", Number(e.target.value))} className={input} />
              </Field>
              <Field label="Minute (0–59)">
                <input type="number" min={0} max={59} value={form.cron_minute ?? 0}
                  onChange={e => set("cron_minute", Number(e.target.value))} className={input} />
              </Field>
            </div>

            {/* Enable toggle */}
            <div className="flex items-center justify-between py-1">
              <div>
                <p className="text-sm font-medium">Auto-run enabled</p>
                <p className="text-xs text-gray-400 dark:text-[#64748b]">Pipeline fires automatically on schedule</p>
              </div>
              <Toggle value={!!form.cron_enabled} onChange={v => set("cron_enabled", v)} />
            </div>
          </Card>

          {/* Budget */}
          <Card icon={<Gauge size={16} />} title="Pipeline Budget" subtitle="Limit API calls per run">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Agent calls / run">
                <input type="number" min={1} value={form.agent_budget_per_run ?? 250}
                  onChange={e => set("agent_budget_per_run", Number(e.target.value))} className={input} />
                <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">TinyFish agent credits</p>
              </Field>
              <Field label="LLM hard stop">
                <input type="number" min={1} value={form.llm_budget_hard_stop ?? 500}
                  onChange={e => set("llm_budget_hard_stop", Number(e.target.value))} className={input} />
                <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">Total LLM calls cap</p>
              </Field>
            </div>
          </Card>

        </div>
      </div>

      {/* ── Social trend scan ── */}
      <SocialScanCard form={form} set={set} apifyConfigured={!!settings?.apify_configured} />

      {/* ── Save button ── */}
      <div className="flex flex-wrap items-center gap-4">
        <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
          className={cn(
            "flex items-center gap-2 px-6 py-2.5 rounded-lg font-semibold text-sm transition-all whitespace-nowrap",
            saveState === "saved" ? "bg-green-600 text-white shadow-lg shadow-green-600/20" :
            saveState === "error" ? "bg-red-600 text-white" :
            "bg-pharma-blue text-white hover:bg-pharma-light dark:bg-[#2563eb] dark:hover:bg-[#3b82f6] shadow-md shadow-pharma-blue/20 disabled:opacity-50"
          )}>
          <Save size={16} />
          {saveState === "saved" ? "Saved!" : saveState === "error" ? "Save failed" : "Save Settings"}
        </button>
        {saveState === "idle" && (
          <p className="text-xs text-gray-400 dark:text-[#64748b]">Changes apply on next pipeline run.</p>
        )}
      </div>

      {/* ── Danger zone ── */}
      <DestroyZone />

    </div>
  );
}

// ── Social Trend Scan ────────────────────────────────────

const SOCIAL_PLATFORMS = [
  { value: "instagram", label: "Instagram" },
  { value: "twitter",   label: "X / Twitter" },
  { value: "linkedin",  label: "LinkedIn" },
  { value: "facebook",  label: "Facebook" },
];

function SocialScanCard({ form, set, apifyConfigured }: {
  form: Partial<AppSettings>; set: (k: string, v: unknown) => void; apifyConfigured: boolean;
}) {
  const qc = useQueryClient();
  const keywords = form.social_keywords ?? [];
  const platforms = form.social_platforms ?? [];
  const fbPages = form.facebook_page_urls ?? [];

  const { data: status } = useQuery({
    queryKey: ["social-status"],
    queryFn: api.social.status,
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 30000),
  });
  const scanMut = useMutation({
    mutationFn: () => api.social.scan(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["social-status"] }),
  });
  const clearSocialMut = useMutation({
    mutationFn: api.social.clearPosts,
    onSuccess: (d) => {
      alert(`Deleted ${d.deleted} social posts.`);
      qc.invalidateQueries({ queryKey: ["social-trends-all"] });
      qc.invalidateQueries({ queryKey: ["social-brief"] });
    },
  });

  const running = status?.running;

  function togglePlatform(p: string) {
    const next = platforms.includes(p) ? platforms.filter(x => x !== p) : [...platforms, p];
    set("social_platforms", next);
  }

  return (
    <Card icon={<Flame size={16} />} title="Social Trend Scan"
      subtitle="Scrape Instagram, X, LinkedIn & Facebook via Apify for trending medical / Roche topics">

      {!apifyConfigured && (
        <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400
          bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/40 rounded-lg px-3 py-2.5">
          <Info size={13} className="shrink-0 mt-0.5" />
          <span>Set <code className="font-mono">APIFY_API_TOKEN</code> in <code className="font-mono">.env</code> to enable scanning.</span>
        </div>
      )}

      {/* Keywords */}
      <Field label="Keywords & hashtags (medical field, drugs, treatments)">
        <textarea
          value={keywords.join(", ")}
          onChange={e => set("social_keywords",
            e.target.value.split(/[,\n]/).map(s => s.trim()).filter(Boolean))}
          rows={3}
          placeholder="Tecentriq, oncology, lungcancer, immunotherapy…"
          className={`${input} font-mono resize-y`}
        />
        <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">{keywords.length} terms — comma or newline separated</p>
      </Field>

      {/* Facebook page URLs */}
      <Field label="Facebook pages to scrape (one URL per line)">
        <textarea
          value={fbPages.join("\n")}
          onChange={e => set("facebook_page_urls",
            e.target.value.split(/[\n,]/).map(s => s.trim()).filter(Boolean))}
          rows={4}
          placeholder="https://www.facebook.com/roche&#10;https://www.facebook.com/Novartis"
          className={`${input} font-mono text-xs resize-y`}
        />
        <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">
          {fbPages.length} pages — Facebook scraping uses known page URLs, not keyword search
        </p>
      </Field>

      {/* Platforms */}
      <Field label="Platforms">
        <div className="flex flex-wrap gap-2">
          {SOCIAL_PLATFORMS.map(p => (
            <button key={p.value} type="button" onClick={() => togglePlatform(p.value)}
              className={cn(
                "px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                platforms.includes(p.value)
                  ? "bg-pharma-blue text-white border-pharma-blue dark:bg-[#2563eb] dark:border-[#2563eb]"
                  : "border-gray-200 dark:border-[#1e3a5f] text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light"
              )}>
              {p.label}
            </button>
          ))}
        </div>
      </Field>

      {/* Window + max per query */}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Time window (days)">
          <input type="number" min={1} value={form.social_window_days ?? 180}
            onChange={e => set("social_window_days", Number(e.target.value))} className={input} />
          <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">Only posts newer than this</p>
        </Field>
        <Field label="Max posts / query">
          <input type="number" min={1} value={form.social_max_per_query ?? 30}
            onChange={e => set("social_max_per_query", Number(e.target.value))} className={input} />
          <p className="text-xs text-gray-400 dark:text-[#64748b] mt-1">Higher = more Apify cost</p>
        </Field>
      </div>

      {/* Include KOLs toggle */}
      <div className="flex items-center justify-between py-1">
        <div>
          <p className="text-sm font-medium">Include KOLs</p>
          <p className="text-xs text-gray-400 dark:text-[#64748b]">Also search KOL names (X & Facebook)</p>
        </div>
        <Toggle value={!!form.social_include_kols} onChange={v => set("social_include_kols", v)} />
      </div>

      {/* Language / geography filter */}
      <div className="flex items-center justify-between py-1">
        <div>
          <p className="text-sm font-medium">Geographic scope</p>
          <p className="text-xs text-gray-400 dark:text-[#64748b]">
            France only: filters posts by French language + applies <code>lang:fr</code> on X/Twitter.
            Global: collects all languages.
          </p>
        </div>
        <select
          value={form.social_lang_filter ?? "fr"}
          onChange={e => set("social_lang_filter", e.target.value)}
          className="px-3 py-1.5 text-sm border border-gray-200 dark:border-[#1e3a5f] rounded-lg bg-transparent"
        >
          <option value="fr">France only (French)</option>
          <option value="en">English only</option>
          <option value="all">Global (all languages)</option>
        </select>
      </div>

      {/* Schedule */}
      <div className="pt-1 border-t border-gray-100 dark:border-[#1e3a5f]/50">
        <div className="flex items-center justify-between py-1">
          <div>
            <p className="text-sm font-medium">Auto-run on schedule</p>
            <p className="text-xs text-gray-400 dark:text-[#64748b]">
              When on, the scan runs automatically (weekly = Mondays). Off = manual button only.
            </p>
          </div>
          <Toggle value={!!form.social_scan_enabled} onChange={v => set("social_scan_enabled", v)} />
        </div>

        {form.social_scan_enabled && (
          <div className="grid grid-cols-2 gap-3 mt-2">
            <Field label="Schedule frequency">
              <div className="grid grid-cols-2 gap-2">
                {(["weekly", "daily"] as const).map(f => (
                  <button key={f} type="button" onClick={() => set("social_scan_frequency", f)}
                    className={cn(
                      "py-2 rounded-lg text-sm font-medium border transition-colors",
                      (form.social_scan_frequency ?? "weekly") === f
                        ? "bg-pharma-blue text-white border-pharma-blue dark:bg-[#2563eb] dark:border-[#2563eb]"
                        : "border-gray-200 dark:border-[#1e3a5f] text-gray-500 dark:text-[#94a3b8] hover:border-pharma-light"
                    )}>
                    {f === "weekly" ? "Weekly" : "Daily"}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Hour (0–23)">
              <input type="number" min={0} max={23} value={form.social_scan_hour ?? 6}
                onChange={e => set("social_scan_hour", Number(e.target.value))} className={input} />
            </Field>
          </div>
        )}
      </div>

      {/* Run scan now */}
      <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-gray-100 dark:border-[#1e3a5f]/50">
        <button onClick={() => scanMut.mutate()} disabled={!apifyConfigured || running || scanMut.isPending}
          className={cn(
            "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50",
            "bg-orange-500 text-white hover:bg-orange-600"
          )}>
          {running
            ? <><Loader2 size={14} className="animate-spin" /> Scanning…</>
            : <><Play size={14} /> Run Social Scan</>}
        </button>

        <button onClick={() => {
            if (confirm("Delete ALL social posts? This only affects social_posts table — KOL data is untouched.")) clearSocialMut.mutate();
          }}
          disabled={clearSocialMut.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-red-300 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50">
          {clearSocialMut.isPending
            ? <><Loader2 size={14} className="animate-spin" /> Clearing…</>
            : <>Clear Social Posts</>}
        </button>
        {running && (
          <span className="text-xs text-gray-500 dark:text-[#94a3b8]">
            {status?.done ?? 0}/{status?.total ?? 0} jobs · {status?.inserted ?? 0} posts found
          </span>
        )}
        {!running && status?.inserted != null && status?.finished_at && (
          <span className="text-xs text-green-600 dark:text-green-400">Last scan: {status.inserted} posts found</span>
        )}
        {status?.error && <span className="text-xs text-red-500">{status.error}</span>}
      </div>
      <p className="text-xs text-gray-400 dark:text-[#64748b]">
        Save your settings first, then run the scan so it uses the latest keywords.
      </p>
    </Card>
  );
}

// ── Destroy Zone ─────────────────────────────────────────

function DestroyZone() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [pass, setPass] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const resetMut = useMutation({
    mutationFn: api.runs.resetAll,
    onSuccess: () => {
      setDone(true);
      setOpen(false);
      setPass("");
      // Clear all cached query data — agent history, discovery, insights, runs, stats
      qc.clear();
    },
    onError: () => setError("Reset failed. Try again."),
  });

  function confirm() {
    if (pass !== "78951") { setError("Wrong password."); return; }
    setError("");
    resetMut.mutate();
  }

  return (
    <div className="border border-red-200 dark:border-red-900/40 rounded-xl p-5 bg-red-50/50 dark:bg-red-950/10">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-red-700 dark:text-red-400">Danger Zone</h3>
          <p className="text-xs text-red-500 dark:text-red-500/70 mt-0.5">
            Wipes all posts, insights, summaries, run history, PDFs, discovery results, chat history, and flushes all queues. Targets and settings are kept.
          </p>
        </div>
        <button
          onClick={() => { setOpen(true); setDone(false); setError(""); setPass(""); }}
          className="flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 transition-colors whitespace-nowrap self-start sm:self-auto"
        >
          <Trash2 size={14} /> Destroy All Data
        </button>
      </div>

      {done && (
        <p className="mt-3 text-xs text-green-600 dark:text-green-400 font-medium">
          All data wiped successfully. Please restart the worker service on Railway to clear its state.
        </p>
      )}

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="glass rounded-xl border border-red-200 dark:border-red-900/40 shadow-xl p-6 w-full max-w-sm space-y-4">
            <div className="flex items-center gap-2 text-red-600">
              <Trash2 size={18} />
              <h3 className="font-semibold">Confirm Destroy</h3>
            </div>
            <p className="text-sm text-gray-600 dark:text-[#94a3b8]">
              This will permanently delete all data. Enter the password to continue.
            </p>
            <input
              type="password"
              value={pass}
              onChange={e => { setPass(e.target.value); setError(""); }}
              onKeyDown={e => e.key === "Enter" && confirm()}
              placeholder="Password"
              autoFocus
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-[#1e3a5f] bg-white dark:bg-[#0a0f1e] text-gray-900 dark:text-[#e2e8f0] focus:outline-none focus:ring-2 focus:ring-red-400/50"
            />
            {error && <p className="text-xs text-red-500">{error}</p>}
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => { setOpen(false); setPass(""); setError(""); }}
                className="flex-1 px-4 py-2 rounded-lg text-sm border border-gray-200 dark:border-[#1e3a5f] text-gray-500 hover:border-gray-300 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirm}
                disabled={resetMut.isPending}
                className="flex-1 px-4 py-2 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {resetMut.isPending ? "Wiping…" : "Destroy"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Shared styles ─────────────────────────────────────────

const input = [
  "w-full px-3 py-2 text-sm rounded-lg",
  "border border-gray-200 dark:border-[#1e3a5f]",
  "bg-white dark:bg-[#0a0f1e]",
  "text-gray-900 dark:text-[#e2e8f0]",
  "focus:outline-none focus:ring-2 focus:ring-pharma-light/50 dark:focus:ring-[#2563eb]/50",
  "transition-colors",
].join(" ");

// ── Sub-components ────────────────────────────────────────

function Card({ icon, title, subtitle, children, collapsible = false, defaultCollapsed = false, headerRight }: {
  icon: React.ReactNode; title: string; subtitle?: string; children: React.ReactNode;
  collapsible?: boolean; defaultCollapsed?: boolean; headerRight?: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const open = !collapsible || !collapsed;
  return (
    <div className="glass-panel rounded-xl shadow-sm overflow-hidden">
      {/* Card header */}
      <div className={cn(
        "px-5 py-4 border-b border-gray-100 dark:border-[#1e3a5f]/60 bg-gray-50/50 dark:bg-[#0a0f1e]/40",
        collapsible && "cursor-pointer select-none",
        collapsible && !open && "border-b-transparent")}
        onClick={collapsible ? () => setCollapsed(c => !c) : undefined}>
        <div className="flex items-center gap-2">
          <span className="text-pharma-light dark:text-[#2563eb]">{icon}</span>
          <div>
            <h2 className="font-semibold text-sm text-gray-900 dark:text-[#e2e8f0]">{title}</h2>
            {subtitle && <p className="text-xs text-gray-400 dark:text-[#64748b]">{subtitle}</p>}
          </div>
          {(headerRight || collapsible) && (
            <div className="ml-auto flex items-center gap-2">
              {headerRight}
              {collapsible && (open
                ? <ChevronDown size={16} className="text-gray-400" />
                : <ChevronRight size={16} className="text-gray-400" />)}
            </div>
          )}
        </div>
      </div>
      {open && <div className="p-5 space-y-4">{children}</div>}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-[#64748b]">
        {label}
      </label>
      {children}
    </div>
  );
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" onClick={() => onChange(!value)}
      className={cn(
        "relative w-11 h-6 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-pharma-light/50",
        value ? "bg-pharma-light dark:bg-[#2563eb]" : "bg-gray-200 dark:bg-[#1e3a5f]"
      )}>
      <span className={cn(
        "block w-4 h-4 rounded-full bg-white shadow-md transition-transform absolute top-1",
        value ? "translate-x-6" : "translate-x-1"
      )} />
    </button>
  );
}

function ModelField({ label, value, onChange, suggestions }: {
  label: string; value: string; onChange: (v: string) => void; suggestions: string[];
}) {
  return (
    <Field label={label}>
      {suggestions.length > 0 ? (
        <div className="relative">
          <select value={value} onChange={e => onChange(e.target.value)} className={`${input} appearance-none pr-8 font-mono`}>
            {!suggestions.includes(value) && value && <option value={value}>{value}</option>}
            {suggestions.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-2.5 top-3 text-gray-400 pointer-events-none" />
        </div>
      ) : (
        <input value={value} onChange={e => onChange(e.target.value)}
          placeholder="e.g. gemini-2.5-flash" className={`${input} font-mono`} />
      )}
    </Field>
  );
}
