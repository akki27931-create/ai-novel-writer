/** 与后端 FastAPI 对齐的 TypeScript 类型定义。 */

export interface Book {
  id: number
  title: string
  author: string
  source: string
  source_id: string
  /** imported = 导入别人的书续写；original = 从零原创 */
  kind: string
  intro: string
  cover_url: string
  total_chapters: number
  analyzed: boolean
  created_at: string
  updated_at: string
}

export interface ChapterBrief {
  id: number
  number: number
  title: string
  word_count: number
  is_original: boolean
  origin: string
  vectorized: boolean
}

export interface ChapterOut extends ChapterBrief {
  content: string
  summary: string
}

export interface BookStats {
  chapters: number
  original_chapters: number
  ai_chapters: number
  words: number
  vectorized: number
  generations: number
}

export type AnalyzeSection =
  | 'all'
  | 'characters'
  | 'worldview'
  | 'timeline'
  | 'foreshadows'
  | 'style'
  | 'outline'

export interface Analysis {
  book_id: number
  synopsis: string
  outline: any
  characters: any[]
  worldview: any[]
  timeline: any[]
  foreshadows: any[]
  style: any
  chapter_summaries: any[]
  /** 原创模式的逐章计划 */
  chapter_plan: any[]
  updated_at: string | null
}

/** 单章的剧情状态增量 */
export interface ChapterState {
  id: number
  chapter_number: number
  summary: string
  location: string
  time_note: string
  present_characters: any[]
  items: any[]
  known_facts: any[]
  open_threads: any[]
  new_foreshadows: any[]
  resolved_foreshadows: any[]
}

/** 人物的滚动状态快照 */
export interface CharacterState {
  id: number
  name: string
  importance: string
  identity: string
  location: string
  doing: string
  mood: string
  goal: string
  status: string
  items: any[]
  knows: any[]
  last_seen_chapter: number
}

export interface StoryState {
  book_id: number
  /** 真正会喂给模型的那段状态文本 */
  snapshot: string
  chapters: ChapterState[]
  characters: CharacterState[]
  missing_chapters: number[]
  state_model: string
  auto_extract: boolean
}

export interface ExtractStatesRequest {
  model?: string | null
  provider_id?: number | null
  upto?: number | null
  batch_size?: number
  limit?: number
}

export interface OriginalBookRequest {
  title: string
  genre: string
  protagonist?: string
  hook?: string
  style_hint?: string
  total_chapters: number
  target_words: number
  extra?: string
  model?: string | null
  provider_id?: number | null
  plan_chapters?: number
  /** 每次让模型输出几章计划（越小越快、越不容易被 max_tokens 截断） */
  plan_batch_size?: number
}

export interface PlanChaptersRequest {
  start_chapter: number
  count: number
  model?: string | null
  provider_id?: number | null
  replace?: boolean
  batch_size?: number
}

export type JobStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled'

export interface Job {
  id: string
  kind: string
  status: JobStatus
  progress: number
  message: string
  total: number
  done: number
  result: any
  error: string
  created_at: string
}

export interface Generation {
  id: number
  book_id: number
  chapter_number: number
  mode: string
  model: string
  goal: string
  target_words: number
  result: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_cny: number
  duration_ms: number
  status: string
  error: string
  saved_chapter_id: number | null
  created_at: string
}

export type GenerateMode = 'continue' | 'outline' | 'polish' | 'rewrite'

export interface GenerateRequest {
  book_id: number
  chapter_number?: number | null
  goal: string
  target_words: number
  model?: string | null
  /** 指定 LLM 网关；留空用「设置」里激活的那个 */
  provider_id?: number | null
  mode: GenerateMode
  source_text?: string
  temperature?: number | null
  retrieval_top_k?: number | null
  auto_save?: boolean
  previous_chapters?: number
  auto_goal?: boolean
  chapter_title?: string
}

export interface BatchGenerateRequest {
  book_id: number
  count: number
  target_words: number
  model?: string | null
  provider_id?: number | null
  previous_chapters?: number
  start_chapter?: number | null
}

export interface BatchChapterResult {
  generation_id: number
  chapter_id: number | null
  number: number
  title: string
  words: number
  warning: string
}

export interface BatchResult {
  count: number
  chapters: BatchChapterResult[]
}

export interface RetrievedChunk {
  chapter_number: number
  chapter_title: string
  score: number
  text: string
  /** semantic = 语义召回；keyword = 关键词必召回（关键锚点） */
  recall?: string
}

export interface GenerateContext {
  chapter_number: number
  model: string
  prompt: string
  outline_text: string
  core_settings_text: string
  foreshadow_text: string
  /** 截至上一章的剧情状态快照 */
  story_state: string
  previous_tail: string
  retrieved: RetrievedChunk[]
  goal: string
  title: string
  auto_planned: boolean
  plan: Record<string, unknown> | null
}

export interface AppSettings {
  api_key_present: boolean
  api_key_source: 'env' | 'db' | 'none'
  base_url: string
  text_model: string
  reasoning_model: string
  active_provider_id: number | null
  temperature: number
  top_p: number
  max_tokens: number
  retrieval_top_k: number
  /** 单次 LLM 请求超时（秒） */
  llm_timeout: number
  /** 失败自动重试次数（不含首次） */
  llm_max_retries: number
  /** 排章节计划时每次让模型输出几章 */
  plan_batch_size: number
  keyword_recall_enabled: boolean
  keyword_recall_limit: number
  embedding_model: string
  // 剧情状态相关
  auto_extract_state: boolean
  state_model: string
  previous_full_chapter: boolean
  previous_tail_chars: number
  state_snapshot_budget: number
  usd_to_cny: number
  pricing: Record<string, { input: number; cached_input: number; output: number }>
  fanqie_mcp_command: string
  fanqie_api_base: string
  prompt_overrides: Record<string, string>
}

/** 一个 LLM 网关（OpenAI 兼容） */
export interface Provider {
  id: number
  name: string
  base_url: string
  text_model: string
  reasoning_model: string
  cheap_model: string
  pricing: Record<string, { input: number; cached_input: number; output: number }>
  note: string
  is_active: boolean
  api_key_present: boolean
  api_key_masked: string
}

export interface ProviderList {
  providers: Provider[]
  active_provider_id: number | null
  resolved: {
    provider_id?: number | null
    provider_name?: string
    base_url?: string
    text_model?: string
    reasoning_model?: string
    cheap_model?: string
    api_key_present?: boolean
  }
}

export interface SettingsResponse {
  settings: AppSettings
  candidate_models: string[]
  providers: Provider[]
  active_provider: Provider | null
}

export interface ModelInfo {
  id: string
  owned_by: string
}

export interface ModelValidation {
  ok: boolean
  error?: string
  models: string[]
  /** missing = 该档位还没配置任何模型名 */
  issues: { field: string; label: string; configured: string; missing?: boolean }[]
  suggestion: { text_model?: string; reasoning_model?: string; cheap_model?: string }
}

export interface SystemInfo {
  version: string
  data_dir: string
  vector_backend?: string
  embedding_mode?: string
  embedding_model?: string
  embedding_dimension?: number
  error?: string
}

export interface UsageRow {
  key: any
  requests: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_cny: number
}

export interface UsageRecent {
  id: number
  book_id: number | null
  task_type: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_cny: number
  created_at: string | null
}

export interface UsageSummary {
  total_requests: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  total_cost_cny: number
  by_model: UsageRow[]
  by_task: UsageRow[]
  by_book: UsageRow[]
  recent: UsageRecent[]
}

export interface PromptItem {
  key: string
  name: string
  description: string
  template: string
  builtin: string
  is_custom: boolean
}

export interface FanqieStatus {
  configured: boolean
  available: boolean
  command: string
  tools: string[]
  message: string
}

export interface FanqieSelftest {
  ok: boolean
  api_base: string
  steps: { step: string; ok: boolean; detail: string }[]
}

export interface ConsistencyIssue {
  type: string
  detail: string
  suggestion: string
  severity: string
}

export interface ConsistencyResult {
  report: { issues: ConsistencyIssue[]; overall: string }
  model: string
  usage: { input_tokens: number; output_tokens: number }
}

/** SSE 流式生成事件 */
export type StreamEvent =
  | {
      type: 'meta'
      generation_id: number
      chapter_number: number
      model: string
      prompt: string
      story_state?: string
      retrieved: RetrievedChunk[]
      goal?: string
      title?: string
      auto_planned?: boolean
      plan?: Record<string, unknown> | null
    }
  | { type: 'delta'; text: string }
  /** 生成过程中的阶段提示（例如「正在抽取本章剧情状态…」） */
  | { type: 'status'; message: string }
  | {
      type: 'done'
      generation_id: number
      chapter_number: number
      model: string
      input_tokens: number
      output_tokens: number
      cost_usd: number
      cost_cny: number
      word_count: number
      saved_chapter_id: number | null
      truncated?: boolean
      warning?: string
    }
  | { type: 'error'; message: string; generation_id?: number }
