// In dev: proxied to localhost:8009 via Vite
// In prod (Railway): VITE_API_URL=https://your-backend.railway.app
import { useAuthStore } from "@/store/auth";

declare const __API_BASE__: string;
const BASE = (typeof __API_BASE__ !== "undefined" && __API_BASE__ ? __API_BASE__ : "") + "/api";

function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    // Session expired / not authenticated → drop creds and bounce to login
    if (res.status === 401 && !path.startsWith("/auth/login")) {
      useAuthStore.getState().logout();
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  // 204 No Content or empty body — don't try to parse JSON
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// ── Types ─────────────────────────────────────────────────
export interface Target {
  id: number;
  name: string;
  known_urls: string[];
  notes: string | null;
  active: boolean;
  disease_area: string | null;
  twitter_handle: string | null;
  linkedin_url: string | null;
}

export interface RunOut {
  id: number;
  status: string;
  started_at: string;
  completed_at: string | null;
  total_targets: number;
  targets_processed: number;
  new_posts_found: number;
  insights_extracted: number;
  pdfs_generated: number;
  current_target: string | null;
  error_message: string | null;
  llm_calls_used: number;
}

export interface Stats {
  active_targets: number;
  total_insights: number;
  today_insights: number;
  last_run_at: string | null;
  last_run_status: string | null;
}

export interface Insight {
  id: number;
  target_name: string;
  topic: string;
  what_they_said: string;
  context: string | null;
  sentiment: string;
  category: string;
  extracted_at: string;
  source_url: string | null;
  source_name: string | null;
  published_date: string | null;
}

export interface AppSettings {
  llm_provider: string;
  llm_model: string;
  ollama_base_url: string;
  nvidia_base_url: string;
  custom_base_url: string | null;
  cron_hour: number;
  cron_minute: number;
  cron_enabled: boolean;
  cron_frequency: string;
  cron_day_of_week: number;
  agent_budget_per_run: number;
  llm_budget_hard_stop: number;
  available_providers: Record<string, string>;
  social_keywords: string[];
  social_platforms: string[];
  social_window_days: number;
  social_max_per_query: number;
  social_scan_enabled: boolean;
  social_scan_frequency: string;
  social_scan_hour: number;
  social_include_kols: boolean;
  facebook_page_urls: string[];
  apify_configured: boolean;
  social_lang_filter: string;
}

export interface SocialPost {
  id: number;
  platform: string;
  post_url: string;
  author: string | null;
  text: string;
  thumbnail_url: string | null;
  likes: number;
  comments: number;
  views: number;
  shares: number;
  hashtags: string[];
  topic: string;
  kind: string;
  posted_at: string | null;
  trend_score: number;
  has_description: boolean;
  language: string;
}

export interface SocialTopic {
  topic: string;
  count: number;
  engagement: number;
  score: number;
  platforms: string[];
}

export interface SocialTrends {
  period_days: number;
  total: number;
  top_posts: SocialPost[];
  top_topics: SocialTopic[];
}

export interface SocialTimeseries {
  topics: string[];
  series: Record<string, string | number>[];
}

export interface SocialScanStatus {
  running: boolean;
  error?: string | null;
  total?: number;
  done?: number;
  inserted?: number;
  started_at?: string;
  finished_at?: string;
}

export interface DiscoveryResult {
  id: number;
  query: string;
  url: string;
  title: string | null;
  snippet: string | null;
  content: string | null;
  source_name: string | null;
  published_date: string | null;
  scraped_at: string;
  from_cache: boolean;
  media_type: "article" | "video" | "pdf" | "linkedin" | "twitter" | "social" | "research";
  thumbnail_url: string | null;
  language: string;
  llm_description: string | null;
}

export interface DailyBriefPoint {
  text: string;
  source: "kol" | "social" | "both";
  priority: "high" | "medium";
}

export interface DailyBrief {
  points: DailyBriefPoint[];
  generated_at: string | null;
  cached: boolean;
  kol_count: number;
  social_count: number;
  error?: string | null;
}

export interface KolInsight {
  id: number;
  kol: string;
  topic: string;
  what_they_said: string;
  sentiment: string;
  category: string;
  published_date: string;
  source_url: string | null;
  source_name: string | null;
  extracted_at: string;
}

export interface SocialSynthesis {
  takeaway: string;
  so_what: string;
  conclusion: string;
  highlights: (SocialPost & { why: string })[];
  total_posts: number;
  generated_at: string | null;
  cached: boolean;
  error?: string | null;
}

export interface DiscoverySynthesis {
  takeaway: string;
  so_what: string;
  conclusion: string;
  highlights: (DiscoveryResult & { why: string })[];
  total: number;
  generated_at: string | null;
  cached: boolean;
  error?: string | null;
}

export interface CombinedSynthesis {
  takeaway: string;
  so_what: string;
  conclusion: string;
  focus: string[];
  kol_count: number;
  social_count: number;
  generated_at: string | null;
  cached: boolean;
  error?: string | null;
}

export interface ProviderHealth {
  id: string;
  name: string;
  configured: boolean;
  status: "ok" | "low" | "exhausted" | "error" | "unknown";
  usage_usd: number | null;
  limit_usd: number | null;
  percent: number | null;
  usage_label?: string | null;
  message: string;
  checked_at: string;
}

export interface DiscoveryContent {
  content: string | null;
  media_type: string;
  youtube_id?: string;
  blocked: boolean;
  error?: string;
  thumbnail_url?: string | null;
}

export interface TopicsData {
  period_days: number;
  total: number;
  categories: { name: string; count: number }[];
  top_topics: { topic: string; count: number; trend_score: number; likes: number; views: number; url?: string | null }[];
  sentiment: { name: string; count: number }[];
  top_kols: { name: string; count: number }[];
}

// ── API calls ─────────────────────────────────────────────
export const api = {
  stats: () => req<Stats>("/stats"),
  combinedSynthesis: (refresh = false) =>
    req<CombinedSynthesis>(`/stats/synthesis${refresh ? "?refresh=true" : ""}`),
  dailyBrief: (refresh = false) => req<DailyBrief>(`/stats/daily-brief${refresh ? "?refresh=true" : ""}`),
  kolBrief: (refresh = false) => req<{
    points: DailyBriefPoint[];
    kol_count: number;
    social_count: number;
    generated_at: string | null;
    cached: boolean;
    error?: string | null;
  }>(`/stats/kol-brief${refresh ? "?refresh=true" : ""}`),
  comparisonBrief: (refresh = false) => req<{
    points: DailyBriefPoint[];
    kol_count: number;
    social_count: number;
    generated_at: string | null;
    cached: boolean;
    error?: string | null;
  }>(`/stats/comparison-brief${refresh ? "?refresh=true" : ""}`),
  socialBrief: (refresh = false) => req<{
    sections: { sector: string; key_signal: string; points: DailyBriefPoint[] }[];
    points: DailyBriefPoint[];
    total_posts: number;
    top_topics: { topic: string; count: number; engagement: number }[];
    generated_at: string | null;
    cached: boolean;
    error?: string | null;
  }>(`/stats/social-brief${refresh ? "?refresh=true" : ""}`),
  socialDetail: (point: string) => req<{
    point: string; summary: string; so_what: string; action: string;
    urgency: string; hashtags: string[];
    total_likes: number; total_comments: number;
    platform_stats: Record<string, { count: number; likes: number; comments: number }>;
    posts: { platform: string; text: string; likes: number; comments: number; shares: number; url: string; topic: string | null; posted_at: string | null }[];
  }>("/stats/social-detail", { method: "POST", body: JSON.stringify({ point }) }),
  briefDetail: (point: string) => req<{
    point: string; summary: string; so_what: string; action: string;
    kol_insights: { kol: string; topic: string | null; said: string; sentiment: string | null }[];
    social_posts: { platform: string; text: string; likes: number; url: string }[];
    links: { url: string; title: string }[];
  }>("/stats/brief-detail", { method: "POST", body: JSON.stringify({ point }) }),
  topics: (days = 7, diseaseArea?: string) => req<TopicsData>(`/stats/topics?days=${days}${diseaseArea && diseaseArea !== "all" ? `&disease_area=${diseaseArea}` : ""}`),

  targets: {
    list: () => req<Target[]>("/targets/"),
    create: (body: Partial<Target>) => req<Target>("/targets/", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<Target>) =>
      req<Target>(`/targets/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    deactivate: (id: number) => req<void>(`/targets/${id}`, { method: "DELETE" }),
  },

  runs: {
    list: () => req<RunOut[]>("/runs/"),
    current: () => req<{ running: boolean } & Partial<RunOut>>("/runs/current"),
    trigger: (limit?: number) =>
      req<{ run_id: number }>("/runs/trigger", { method: "POST", body: JSON.stringify({ limit }) }),
    stop: () => req<{ stopped: boolean }>("/runs/stop", { method: "POST" }),
    generatePdfs: () => req<{ status: string; run_id: number }>("/runs/generate-pdfs", { method: "POST" }),
    resetAll: () => req<{ db_cleared: boolean; blobs_deleted: number; chroma_reset: boolean }>("/runs/reset-all", { method: "POST" }),
  },

  reports: {
    latest: (limit = 20) => req<Insight[]>(`/reports/latest?limit=${limit}`),
    list: () => req<{ path: string; name: string; size: number; url: string; uploadedAt?: string }[]>("/reports/"),
  },

  settings: {
    get: () => req<AppSettings>("/settings/"),
    update: (body: Partial<AppSettings>) =>
      req<AppSettings>("/settings/", { method: "POST", body: JSON.stringify(body) }),
    fetchModels: (body: { provider?: string }) =>
      req<{ provider: string; models: string[] }>("/settings/models", { method: "POST", body: JSON.stringify(body) }),
    testConnection: (body: { provider?: string; model?: string }) =>
      req<{ ok: boolean }>("/settings/test-connection", { method: "POST", body: JSON.stringify(body) }),
  },

  agent: {
    chat: (message: string) =>
      req<{ reply: string }>("/agent/chat", { method: "POST", body: JSON.stringify({ message }) }),
    history: () => req<{ role: string; content: string; created_at: string }[]>("/agent/history"),
    clearHistory: () => req<void>("/agent/history", { method: "DELETE" }),
  },

  discovery: {
    search: (query: string, forceRefresh = false, lang: string = "fr") =>
      req<{ results: DiscoveryResult[]; from_cache: boolean; count: number }>(
        "/discovery/search",
        { method: "POST", body: JSON.stringify({ query, force_refresh: forceRefresh, lang }) }
      ),
    fetchContent: (result_id: number, url: string) =>
      req<DiscoveryContent>(
        "/discovery/fetch-content",
        { method: "POST", body: JSON.stringify({ result_id, url }) }
      ),
    history: () => req<{ queries: { query: string; scraped_at: string }[] }>("/discovery/history"),
    deepSearch: (q: string, lang: string = "fr") => req<{ results: DiscoveryResult[]; count: number }>(
      "/discovery/deep-search",
      { method: "POST", body: JSON.stringify({ query: q, force_refresh: false, lang }) }
    ),
    describe: (result_id: number) =>
      req<{ description: string; so_what: string | null; cached: boolean }>(
        "/discovery/describe",
        { method: "POST", body: JSON.stringify({ result_id }) }
      ),
    kolMentions: (q: string) => req<{
      recent: KolInsight[];
      historical: KolInsight[];
      total: number;
    }>(`/discovery/kol-mentions?q=${encodeURIComponent(q)}`),
    synthesis: (query: string, lang = "all", refresh = false) =>
      req<DiscoverySynthesis>("/discovery/synthesis", {
        method: "POST",
        body: JSON.stringify({ query, lang, refresh }),
      }),
  },

  social: {
    trends: (days = 180, platform = "all", kind = "all", limit = 60) =>
      req<SocialTrends>(
        `/social/trends?days=${days}&platform=${platform}&kind=${kind}&limit=${limit}`
      ),
    scan: (lang?: string) => req<{ started: boolean; task_id: string; lang: string | null }>(
      `/social/scan${lang ? `?lang=${lang}` : ""}`, { method: "POST" }),
    clearPosts: () => req<{ deleted: number }>("/social/posts", { method: "DELETE" }),
    status: () => req<SocialScanStatus>("/social/status"),
    timeseries: (days = 180, top = 6) =>
      req<SocialTimeseries>(`/social/timeseries?days=${days}&top=${top}`),
    describe: (id: number) =>
      req<{ description: string; so_what: string | null; cached: boolean }>("/social/describe", {
        method: "POST",
        body: JSON.stringify({ id }),
      }),
    discover: (q: string, fresh = true, lang: string = "fr") =>
      req<{ query: string; results: SocialPost[]; fetching: boolean }>(
        `/social/discover?q=${encodeURIComponent(q)}&fresh=${fresh}&lang=${lang}`
      ),
    discoverStatus: (q: string) =>
      req<{ running: boolean; inserted?: number; error?: string; terms?: string[] }>(
        `/social/discover/status?q=${encodeURIComponent(q)}`
      ),
    discoverHistory: () =>
      req<{ queries: { query: string; scraped_at: string }[] }>("/social/discover/history"),
    synthesis: (days = 30, lang = "all", refresh = false) =>
      req<SocialSynthesis>(
        `/social/synthesis?days=${days}&lang=${lang}${refresh ? "&refresh=true" : ""}`
      ),
  },

  health: {
    providers: (refresh = false) =>
      req<{ providers: ProviderHealth[]; checked_at: string }>(
        `/health/providers${refresh ? "?refresh=true" : ""}`
      ),
  },

  genQuota: () => req<{ admin: boolean; features: Record<string, boolean> }>("/me/gen-quota"),

  auth: {
    login: async (email: string, password: string) => {
      const body = new URLSearchParams({ username: email, password });
      const res = await fetch(`${BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      if (!res.ok) {
        let detail = "Login failed";
        try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      return (await res.json()) as { access_token: string; token_type: string; user: AuthUserDTO };
    },
    me: () => req<AuthUserDTO>("/auth/me"),
    updateProfile: (body: { name?: string; email?: string; current_password?: string; new_password?: string }) =>
      req<AuthUserDTO>("/auth/me", { method: "PATCH", body: JSON.stringify(body) }),
    listUsers: () => req<AuthUserDTO[]>("/auth/users"),
    createUser: (name: string, email: string, password: string, role: string) =>
      req<AuthUserDTO>("/auth/users", {
        method: "POST", body: JSON.stringify({ name, email, password, role }),
      }),
    updateUser: (id: number, body: { name?: string; email?: string; password?: string; role?: string; is_active?: boolean }) =>
      req<AuthUserDTO>(`/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteUser: (id: number) => req<void>(`/auth/users/${id}`, { method: "DELETE" }),
  },
};

export interface AuthUserDTO {
  id: number;
  name: string | null;
  email: string;
  role: "admin" | "user";
  is_active: boolean;
  is_superadmin: boolean;
  created_at: string;
}
