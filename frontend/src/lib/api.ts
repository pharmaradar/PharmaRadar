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
  target_type: "kol" | "competitor";
  twitter_handle: string | null;
  linkedin_url: string | null;
}

export interface CompetitorPublication {
  id: number;
  competitor: string;
  title: string | null;
  url: string;
  source: string;
  published_date: string | null;
  likes: number;
  views: number;
  engagement: number;
  excerpt: string;
}

export interface EmergingVoice {
  author: string;
  posts: number;
  engagement: number;
  platforms: string[];
  examples: {
    platform: string; text: string; url: string;
    likes: number; comments: number; posted_at: string | null;
  }[];
}

export type SynthesisScope = "kol" | "competitor" | "comprehensive";

export interface SynthesisSource {
  n: number;
  target: string;
  topic: string;
  url: string;
  source_name: string;
  date: string;
  quote: string;
}

export interface SynthesisKeyPost {
  target: string;
  topic: string;
  said: string;
  url: string;
  source_name: string;
  date: string;
  why: string;
}

/** One downloadable dashboard synthesis: Main information / So what /
 *  Recommendations / What to watch / Key articles & posts, plus the resolved
 *  sources every claim cites. */
export interface SynthesisReport {
  scope: SynthesisScope;
  title: string;
  generated_at: string;
  insight_count: number;
  pdf_url: string | null;
  error?: string | null;
  main: string[];
  so_what: string;
  recommendations: string[];
  watch: string[];
  key_posts: SynthesisKeyPost[];
  sources: SynthesisSource[];
}

export interface SynthesisState {
  scope: SynthesisScope;
  status: "idle" | "running" | "done" | "error";
  error?: string | null;
  result: SynthesisReport | null;
}

export interface GlobalSynthesis {
  exec_summary: string;
  kol_takeaways: string[];
  population_takeaways: string[];
  topic_takeaways: string[];
  so_what: string;
  /** Actionable next steps. Optional: reports generated before this field
   *  existed are still stored and rendered. */
  recommendations?: string[];
  important_posts: (BurningTopicImportantPost & { why?: string })[];
  sections_present: { kol: boolean; population: boolean; burning_topics: number };
  generated_at: string;
  pdf_url: string | null;
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
  /** "weekly" | "monthly" — daily was retired on the client's request. */
  cron_frequency: string;
  cron_day_of_week: number;
  /** 1-28 for monthly mode, capped so no month skips the run. */
  cron_day_of_month: number;
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

export interface KolProfileCard {
  id: number;
  name: string;
  target_type: "kol" | "competitor";
  active: boolean;
  disease_area: string | null;
  twitter_handle: string | null;
  linkedin_url: string | null;
  insight_count: number;
  last_activity: string | null;
  summary_bullets: string[];
  so_what: string | null;
  summary_generated_at: string | null;
}

export interface KolStatement {
  id: number; topic: string; what_they_said: string; sentiment: string;
  category: string; url: string; source_name: string; source_scope: string; date: string;
}

export interface KolProfile extends KolProfileCard {
  known_urls: string[];
  window_days: number;
  sentiment: Record<string, number>;
  top_topics: { topic: string; count: number }[];
  per_week: Record<string, number>;
  statements: KolStatement[];
}

/** Share of voice by product — a brand lead thinks in assets, not topics. */
export interface BrandRow {
  brand: string; owner: string; is_ours: boolean; indication: string;
  mentions: number; share: number;
  sentiment: Record<string, number>;
  /** Net sentiment across rated mentions only; null when nothing carried an opinion. */
  net_sentiment: number | null;
  rated_mentions: number; engagement: number; sources: number;
}

export interface ShareOfVoice {
  window_days: number; source: string; items_scanned: number; tracked_brands: number;
  total_mentions: number; roche_mentions: number; competitor_mentions: number;
  roche_share: number;
  brands: BrandRow[];
  by_owner: { owner: string; is_ours: boolean; mentions: number; share: number; brands: string[] }[];
}

export interface TrackedAccount {
  id: number;
  platform: "twitter" | "linkedin" | "instagram" | "facebook";
  handle: string;
  url: string | null;
  label: string | null;
  category: string | null;
  active: boolean;
  /** Posts attributed to this account so far. 0 means the handle has never
   *  yielded anything — usually a wrong slug, which fails silently otherwise. */
  post_count?: number;
  last_seen?: string | null;
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

/** Ad-hoc market-research report produced by Topic Explorer. Voice distribution
 *  and volume are computed from rows, not written by the model. */
export interface MarketReportVoiceRow {
  bucket: string; label: string; mentions: number; percent: number;
}

export interface MarketReportVolume {
  total: number;
  by_kind: Record<string, number>;
  dated: number;
  /** % of mentions carrying a usable date — the weekly trend covers only these. */
  date_coverage: number;
  per_week: Record<string, number>;
  total_engagement: number;
  window_days: number;
}

export interface MarketReportSource {
  n: number; kind: string; author: string; url: string;
  source_name: string; date: string; quote: string;
}

export interface MarketReportKeyPost {
  kind: string; author: string; url: string; source_name: string;
  date: string; text: string; engagement: number;
  /** Combined line, kept for reports generated before the split below. */
  why: string;
  /** What this specific item claims or reports. */
  says?: string;
  /** How Roche can use it — the action, opening or risk it creates. */
  benefit?: string;
}

export interface MarketReport {
  id: number;
  question: string;
  status: "pending" | "running" | "done" | "failed";
  error?: string | null;
  window_days: number;
  language: string | null;
  exec_summary: string;
  so_what: string;
  what_is_said: string;
  voices_note: string;
  volume_note: string;
  subtopics: string[];
  voice_rows: MarketReportVoiceRow[];
  /** Main speakers on this question. `tracked: false` marks a voice outside the
   *  current audience — a candidate KOL. Replaces the old side panel. */
  main_authors: MarketReportAuthor[];
  volume: MarketReportVolume;
  key_posts: MarketReportKeyPost[];
  sources: MarketReportSource[];
  item_count: number;
  /** % of voices identified from tracked records rather than inferred. */
  voice_exact_share: number;
  pdf_url: string | null;
  created_at: string;
}

export interface MarketReportSummary {
  id: number; question: string; status: string;
  item_count: number; pdf_url: string | null; created_at: string;
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
  /** Provenance recorded at ingest: "fr" when the domain is a French source. */
  source_scope: "fr" | "global";
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

export interface BurningTopic {
  id: number;
  name: string;
  description: string | null;
  language_filter: string | null;
  period_days: number;
  exclusion_words: string[];
  restriction_terms: string[];
  created_by: number | null;
  is_active: boolean;
  created_at: string;
  latest_report: { id: number; status: string; created_at: string; pdf_url: string | null } | null;
}

export interface BurningTopicImportantPost {
  url: string;
  title: string | null;
  author: string | null;
  engagement: number;
  platform?: string;
  kind?: string;
  why?: string;
}

export interface MarketReportAuthor {
  author: string;
  mentions: number;
  engagement: number;
  platforms: string[];
  tracked: boolean;
}

export interface BurningTopicAuthor {
  author: string;
  posts: number;
  engagement: number;
  platforms: string[];
  note?: string | null;
}

export interface ReportQuestionAnswer {
  question_id: number;
  question: string;
  answer: string;
}

export interface BurningTopicReport {
  id: number;
  topic_id: number | null;
  congress_id: number | null;
  status: "pending" | "running" | "done" | "failed";
  summary_md: string | null;
  key_findings: string[];
  so_what: string | null;
  important_posts: BurningTopicImportantPost[];
  main_authors: BurningTopicAuthor[];
  question_answers: ReportQuestionAnswer[];
  /** Market-research sections. Empty on reports generated before they existed —
   *  the UI renders each section only when it has content. */
  what_is_said: string;
  voices_note: string;
  volume_note: string;
  subtopics: string[];
  voice_rows: MarketReportVoiceRow[];
  volume: Partial<MarketReportVolume>;
  item_count: number;
  voice_exact_share: number;
  window_days: number;
  pdf_url: string | null;
  created_at: string;
}

export interface CongressQuestion {
  id: number;
  congress_id: number;
  question_text: string;
  created_at: string;
}

export interface Congress {
  id: number;
  name: string;
  hashtags: string[];
  start_date: string;
  end_date: string;
  disease_area: string | null;
  is_active: boolean;
  created_at: string;
  questions: CongressQuestion[];
  latest_report: { id: number; status: string; created_at: string; pdf_url: string | null } | null;
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

/** A social account the platform collects directly, rather than by keyword luck. */
export interface TrackedAccountFull {
  id: number;
  platform: "twitter" | "linkedin" | "instagram" | "facebook";
  handle: string;
  url: string | null;
  label: string | null;
  full_name: string | null;
  role: string | null;
  category: string | null;
  notes: string | null;
  active: boolean;
  post_count: number;
  analysis?: AccountAnalysis;
  last_scanned_at: string | null;
  /** ok | empty | error — "empty" means the scan ran and found nothing, which
   *  is what a mistyped handle looks like from the outside. */
  last_scan_status: "ok" | "empty" | "error" | null;
}

/** Cached AI read of what a tracked account talks about. `stale` means posts
 *  arrived after it was written, so it covers only `post_count` of them. */
export interface AccountAnalysis {
  summary: string | null;
  so_what: string | null;
  themes: string[];
  generated_at: string | null;
  stale: boolean;
  post_count: number;
  /** Full market-research analysis. Voice distribution and volume are computed
   *  from the rows, not written by the model. */
  /** Blob/local URL of the last exported PDF, if one was generated. */
  pdf_url?: string | null;
  sections?: {
    exec_summary?: string;
    so_what?: string;
    what_is_said?: string;
    voices_note?: string;
    volume_note?: string;
    subtopics?: string[];
    /** Per-post reading: what a specific post says and how we can use it. */
    key_posts?: {
      url: string; author: string; platform: string; date: string;
      engagement: number; comments: number;
      says: string; benefit: string; text: string;
    }[];
    voice_rows?: MarketReportVoiceRow[];
    voice_exact_share?: number;
    volume?: MarketReportVolume;
    item_count?: number;
  };
}

/** Six-section analysis of one post. `voice` and `reach` are computed from the
 *  row, not written by the model. */
export interface PostAnalysis {
  exec_summary: string;
  so_what: string;
  what_is_said: string;
  voice_note: string;
  reach_note: string;
  subtopics: string[];
  voice: { bucket: string; confidence: string; evidence: string };
  reach: {
    available: boolean; likes: number; comments: number; views: number;
    engagement: number; platform_average: number | null;
    vs_average: number | null; platform: string; note: string | null;
  };
}

export interface AccountPost {
  id: number;
  url: string;
  text: string | null;
  /** Only Instagram and Facebook carry images — TinyFish search results (X,
   *  LinkedIn) have none, so cards must render without one. */
  thumbnail_url: string | null;
  platform: string;
  author: string | null;
  likes: number;
  comments: number;
  views: number;
  language: string | null;
  kind: string;
  /** When they published. Null on LinkedIn and X, whose search results carry no
   *  publication date — so the UI shows collected_at and labels it honestly. */
  posted_at: string | null;
  collected_at: string | null;
}

export interface AccountDetail {
  account: TrackedAccountFull;
  window_days: number;
  posts: AccountPost[];
  stats: { posts_in_window: number; total_engagement: number; dated_posts: number };
}

export interface AccountScanStatus {
  running: boolean;
  total?: number;
  done?: number;
  saved?: number;
  current?: string;
  error?: string | null;
  finished_at?: string;
}

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
  competitorBrief: (refresh = false) => req<{
    points: DailyBriefPoint[];
    kol_count: number;
    social_count: number;
    generated_at: string | null;
    cached: boolean;
    error?: string | null;
  }>(`/stats/competitor-brief${refresh ? "?refresh=true" : ""}`),
  competitorPublications: (days = 90, limit = 20) =>
    req<{ period_days: number; total: number; publications: CompetitorPublication[] }>(
      `/stats/competitor-publications?days=${days}&limit=${limit}`
    ),

  targets: {
    list: () => req<Target[]>("/targets/"),
    create: (body: Partial<Target>) => req<Target>("/targets/", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<Target>) =>
      req<Target>(`/targets/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: number) => req<void>(`/targets/${id}?purge=true`, { method: "DELETE" }),
  },

  runs: {
    list: () => req<RunOut[]>("/runs/"),
    current: () => req<{ running: boolean } & Partial<RunOut>>("/runs/current"),
    trigger: (limit?: number) =>
      req<{ run_id: number }>("/runs/trigger", { method: "POST", body: JSON.stringify({ limit }) }),
    stop: () => req<{ stopped: boolean }>("/runs/stop", { method: "POST" }),
    generatePdfs: () => req<{ status: string; run_id: number }>("/runs/generate-pdfs", { method: "POST" }),
    resetAll: () => req<{ db_cleared: boolean; blobs_deleted: number; chroma_reset: boolean }>("/runs/reset-all", { method: "POST" }),
    // Super admin only — backend 403s for everyone else.
    remove: (id: number) =>
      req<{ deleted: number; summaries_detached: number }>(`/runs/${id}`, { method: "DELETE" }),
  },

  reports: {
    latest: (limit = 20) => req<Insight[]>(`/reports/latest?limit=${limit}`),
    list: () => req<{ path: string; name: string; size: number; url: string; uploadedAt?: string }[]>("/reports/"),
    triggerGlobalSynthesis: () =>
      req<{ status: string }>("/reports/global-synthesis", { method: "POST" }),
    // The three downloadable dashboard syntheses.
    triggerSynthesis: (scope: SynthesisScope) =>
      req<{ status: string; scope: SynthesisScope }>(`/reports/synthesis/${scope}`, { method: "POST" }),
    synthesis: (scope: SynthesisScope) =>
      req<SynthesisState>(`/reports/synthesis/${scope}`),
    globalSynthesis: () =>
      req<{ status: "idle" | "running" | "done" | "failed"; error?: string | null; result: GlobalSynthesis | null }>(
        "/reports/global-synthesis"
      ),
    // Blob URLs are public; local-dev /api/... PDF paths need the auth header
    openPdf: async (url: string) => {
      if (!url.startsWith("/api/")) { window.open(url, "_blank", "noopener,noreferrer"); return; }
      const res = await fetch(`${BASE}${url.slice(4)}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`${res.status}: PDF not available`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      window.open(objectUrl, "_blank", "noopener,noreferrer");
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    },
  },

  // KOL module — surfaces the per-target summaries the pipeline already writes.
  kolProfiles: (q?: string, targetType = "kol") =>
    req<{ profiles: KolProfileCard[] }>(
      `/targets/profiles?target_type=${targetType}` + (q ? `&q=${encodeURIComponent(q)}` : "")),
  kolProfile: (id: number, days = 30) =>
    req<KolProfile>(`/targets/${id}/profile?days=${days}`),
  shareOfVoice: (days = 30, source = "all") =>
    req<ShareOfVoice>(`/stats/share-of-voice?days=${days}&source=${source}`),

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
    emergingVoices: (params: { q?: string; days?: number; language?: string; platform?: string } = {}) => {
      const qs = new URLSearchParams();
      if (params.q) qs.set("q", params.q);
      if (params.days) qs.set("days", String(params.days));
      if (params.language && params.language !== "all") qs.set("language", params.language);
      if (params.platform && params.platform !== "all") qs.set("platform", params.platform);
      return req<{ period_days: number; total_authors: number; voices: EmergingVoice[] }>(
        `/discovery/emerging-voices?${qs.toString()}`
      );
    },
    // Market-research report: queue, poll, list.
    createReport: (question: string, windowDays = 30, lang = "fr") =>
      req<{ id: number; status: string }>("/discovery/report", {
        method: "POST",
        body: JSON.stringify({ question, window_days: windowDays, lang }),
      }),
    report: (id: number) => req<MarketReport>(`/discovery/report/${id}`),
    /** The newest finished report for this exact question, or null. Free —
     *  reuse costs nothing, generation draws on the small daily quota. */
    findReport: (question: string, windowDays = 30, lang = "fr") =>
      req<{ report: MarketReport | null }>(
        `/discovery/report/by-question?q=${encodeURIComponent(question)}` +
        `&window_days=${windowDays}&language=${lang}`),
    reports: (limit = 20) =>
      req<{ reports: MarketReportSummary[] }>(`/discovery/reports?limit=${limit}`),
    synthesis: (query: string, lang = "fr", refresh = false) =>
      req<DiscoverySynthesis>("/discovery/synthesis", {
        method: "POST",
        body: JSON.stringify({ query, lang, refresh }),
      }),
  },

  accounts: {
    list: () => req<{
      accounts: TrackedAccountFull[]; platforms: string[]; roles: string[];
      totals: { accounts: number; active: number; producing: number; posts: number };
    }>("/accounts"),
    detail: (id: number, days = 90) =>
      req<AccountDetail>(`/accounts/${id}?days=${days}`),
    create: (body: Partial<TrackedAccountFull>) =>
      req<TrackedAccountFull>("/accounts", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<TrackedAccountFull>) =>
      req<TrackedAccountFull>(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    remove: (id: number) =>
      req<{ deleted: number }>(`/accounts/${id}`, { method: "DELETE" }),
    refresh: (id: number) =>
      req<{ queued: boolean }>(`/accounts/${id}/refresh`, { method: "POST" }),
    scanAll: () => req<{ queued: boolean }>("/accounts/scan", { method: "POST" }),
    reportPdf: (id: number) =>
      req<{ pdf_url: string }>(`/accounts/${id}/report/pdf`, { method: "POST" }),
    analyse: (id: number, refresh = false) =>
      req<TrackedAccountFull & { cached: boolean; posts_analysed?: number }>(
        `/accounts/${id}/analyse?refresh=${refresh}`, { method: "POST" }),
    status: () => req<AccountScanStatus>("/accounts/status"),
  },

  social: {
    // `language` must be sent explicitly: the API defaults to "fr" (France-first),
    // so omitting it silently narrows the pool and breaks the Global toggle.
    trends: (days = 30, platform = "all", kind = "all", limit = 60, language = "fr") =>
      req<SocialTrends>(
        `/social/trends?days=${days}&platform=${platform}&kind=${kind}&limit=${limit}` +
        `&language=${language}`
      ),
    // Tracked accounts registry — the client's "define and track specific accounts".
    accounts: () => req<{ accounts: TrackedAccount[] }>("/social/accounts"),
    createAccount: (body: Partial<TrackedAccount>) =>
      req<TrackedAccount>("/social/accounts", { method: "POST", body: JSON.stringify(body) }),
    updateAccount: (id: number, body: Partial<TrackedAccount>) =>
      req<TrackedAccount>(`/social/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteAccount: (id: number) =>
      req<{ deleted: number }>(`/social/accounts/${id}`, { method: "DELETE" }),
    scan: (lang?: string) => req<{ started: boolean; task_id: string; lang: string | null }>(
      `/social/scan${lang ? `?lang=${lang}` : ""}`, { method: "POST" }),
    clearPosts: () => req<{ deleted: number }>("/social/posts", { method: "DELETE" }),
    status: () => req<SocialScanStatus>("/social/status"),
    timeseries: (days = 30, top = 6) =>
      req<SocialTimeseries>(`/social/timeseries?days=${days}&top=${top}`),
    /** Cached analysis only — never triggers an LLM call. */
    postAnalysis: (id: number) =>
      req<{ sections: PostAnalysis | null; cached: boolean }>(
        `/social/post/${id}/analysis`),
    analysePost: (id: number, refresh = false) =>
      req<{ sections: PostAnalysis; cached: boolean }>(
        `/social/post/${id}/analyse?refresh=${refresh}`, { method: "POST" }),
    describe: (id: number) =>
      req<{ description: string; so_what: string | null; cached: boolean }>("/social/describe", {
        method: "POST",
        body: JSON.stringify({ id }),
      }),
    /** Ad-hoc social search. A multi-word query is treated as a question and
     *  expanded into bilingual terms server-side, so `terms` reports what was
     *  actually matched and `total_matched` the pool before the display cap. */
    /** Ad-hoc social search. A multi-word query is treated as a question and
     *  expanded into bilingual terms server-side, so `terms` reports what was
     *  actually matched and `total_matched` the pool before the display cap.
     *  `cached: true` means this phrase was already collected recently and no
     *  paid fetch was issued — pass `force` to collect again anyway. */
    discover: (q: string, fresh = true, lang: string = "fr", limit = 120,
               force = false) =>
      req<{
        query: string; results: SocialPost[]; fetching: boolean; cached?: boolean;
        terms?: string[]; total_matched?: number;
      }>(
        `/social/discover?q=${encodeURIComponent(q)}&fresh=${fresh}&lang=${lang}` +
        `&limit=${limit}&force=${force}`
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

  burningTopics: {
    list: () => req<BurningTopic[]>("/burning-topics/"),
    create: (body: {
      name: string; description?: string | null; language_filter?: string | null;
      period_days?: number; exclusion_words?: string[]; restriction_terms?: string[];
    }) => req<BurningTopic>("/burning-topics/", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<{
      name: string; description: string | null; language_filter: string | null;
      period_days: number; exclusion_words: string[]; restriction_terms: string[]; is_active: boolean;
    }>) => req<BurningTopic>(`/burning-topics/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: number) => req<void>(`/burning-topics/${id}`, { method: "DELETE" }),
    // periodDays overrides the topic's window for this run only.
    generate: (id: number, periodDays?: number) =>
      req<{ report_id: number; status: string }>(
        `/burning-topics/${id}/generate-report` +
        (periodDays ? `?period_days=${periodDays}` : ""),
        { method: "POST" },
      ),
    reports: (id: number) => req<BurningTopicReport[]>(`/burning-topics/${id}/reports`),
    followup: (topicId: number, reportId: number, question: string, history: { role: string; content: string }[]) =>
      req<{ answer: string }>(`/burning-topics/${topicId}/reports/${reportId}/followup`, {
        method: "POST", body: JSON.stringify({ question, history }),
      }),
    // Dev/blob-less fallback: stream the PDF through the backend with auth
    downloadPdf: async (topicId: number, reportId: number): Promise<Blob> => {
      const res = await fetch(`${BASE}/burning-topics/${topicId}/reports/${reportId}/pdf`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`${res.status}: PDF not available`);
      return res.blob();
    },
  },

  congress: {
    list: () => req<Congress[]>("/congress/"),
    create: (body: {
      name: string; hashtags: string[]; start_date: string; end_date: string; disease_area?: string | null;
    }) => req<Congress>("/congress/", { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: Partial<{
      name: string; hashtags: string[]; start_date: string; end_date: string;
      disease_area: string | null; is_active: boolean;
    }>) => req<Congress>(`/congress/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: number) => req<void>(`/congress/${id}`, { method: "DELETE" }),
    addQuestion: (id: number, question_text: string) =>
      req<CongressQuestion>(`/congress/${id}/questions`, {
        method: "POST", body: JSON.stringify({ question_text }),
      }),
    updateQuestion: (id: number, questionId: number, question_text: string) =>
      req<CongressQuestion>(`/congress/${id}/questions/${questionId}`, {
        method: "PUT", body: JSON.stringify({ question_text }),
      }),
    removeQuestion: (id: number, questionId: number) =>
      req<void>(`/congress/${id}/questions/${questionId}`, { method: "DELETE" }),
    generate: (id: number) =>
      req<{ report_id: number; status: string }>(`/congress/${id}/generate-report`, { method: "POST" }),
    reports: (id: number) => req<BurningTopicReport[]>(`/congress/${id}/reports`),
    downloadPdf: async (id: number, reportId: number): Promise<Blob> => {
      const res = await fetch(`${BASE}/congress/${id}/reports/${reportId}/pdf`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`${res.status}: PDF not available`);
      return res.blob();
    },
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
