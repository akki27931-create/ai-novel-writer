/** 后端接口封装。全部使用相对路径 /api，由 Vite(开发) 或 Nginx(生产) 代理到 FastAPI。 */

import type {
  Analysis,
  AnalyzeSection,
  AppSettings,
  BatchGenerateRequest,
  Book,
  BookStats,
  ChapterBrief,
  ChapterOut,
  ConsistencyResult,
  ExtractStatesRequest,
  FanqieStatus,
  GenerateContext,
  GenerateRequest,
  Generation,
  Job,
  ModelInfo,
  ModelValidation,
  OriginalBookRequest,
  PlanChaptersRequest,
  PromptItem,
  Provider,
  ProviderList,
  FanqieSelftest,
  SettingsResponse,
  StoryState,
  SystemInfo,
  StreamEvent,
  UsageSummary,
} from './types'

/** 统一请求：非 2xx 时抛出后端返回的 detail 文案。 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let data: any = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  if (!res.ok) {
    const detail = data?.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : detail
          ? JSON.stringify(detail, null, 2)
          : `请求失败（HTTP ${res.status}）`,
    )
  }
  return data as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  // ---------------- 系统 ----------------
  health: () => request<{ status: string; version: string }>('/api/health'),
  systemInfo: () => request<SystemInfo>('/api/system/info'),

  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<{ cancelled: boolean }>(`/api/jobs/${id}/cancel`, json({})),

  // ---------------- 书籍 ----------------
  listBooks: (q?: string) =>
    request<Book[]>(`/api/books${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  getBook: (id: number) => request<Book>(`/api/books/${id}`),
  updateBook: (id: number, body: Partial<Book>) =>
    request<Book>(`/api/books/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteBook: (id: number) => request<void>(`/api/books/${id}`, { method: 'DELETE' }),
  bookStats: (id: number) => request<BookStats>(`/api/books/${id}/stats`),

  uploadBook: (file: File, title: string, author: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', title)
    form.append('author', author)
    // 注意：不要手动设置 Content-Type，浏览器会自动带上 multipart boundary
    return request<{ book: Book; chapters_created: number; message: string }>(
      '/api/books/upload',
      { method: 'POST', body: form },
    )
  },

  pasteBook: (body: {
    title: string
    author: string
    content: string
    chapter_title: string
    split: boolean
  }) =>
    request<{ book: Book; chapters_created: number; message: string }>(
      '/api/books/paste',
      json(body),
    ),

  // ---------------- 章节 ----------------
  listChapters: (bookId: number) => request<ChapterBrief[]>(`/api/books/${bookId}/chapters`),
  createChapter: (bookId: number, body: { title: string; content: string; number?: number }) =>
    request<ChapterOut>(`/api/books/${bookId}/chapters`, json({ ...body, is_original: false, origin: 'manual' })),
  getChapter: (id: number) => request<ChapterOut>(`/api/chapters/${id}`),
  updateChapter: (id: number, body: { title?: string; content?: string; summary?: string }) =>
    request<ChapterOut>(`/api/chapters/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteChapter: (id: number) => request<void>(`/api/chapters/${id}`, { method: 'DELETE' }),

  // ---------------- 拆书 ----------------
  getAnalysis: (bookId: number) => request<Analysis>(`/api/books/${bookId}/analysis`),
  updateAnalysis: (bookId: number, body: Record<string, unknown>) =>
    request<Analysis>(`/api/books/${bookId}/analysis`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  startAnalyze: (
    bookId: number,
    body: {
      model?: string | null
      provider_id?: number | null
      batch_size: number
      max_chapters?: number | null
      redo_summaries: boolean
      with_states: boolean
      section: AnalyzeSection
    },
  ) => request<{ job_id: string; kind: string; message: string }>(`/api/books/${bookId}/analyze`, json(body)),
  startVectorize: (bookId: number, body: { force: boolean; max_chapters?: number | null }) =>
    request<{ job_id: string; kind: string; message: string }>(
      `/api/books/${bookId}/vectorize`,
      json(body),
    ),

  // ---------------- 剧情状态层 ----------------
  getStoryState: (bookId: number, upto?: number | null) =>
    request<StoryState>(
      `/api/books/${bookId}/state${upto ? `?upto=${upto}` : ''}`,
    ),
  extractStates: (bookId: number, body: ExtractStatesRequest) =>
    request<{ job_id: string; kind: string; message: string }>(
      `/api/books/${bookId}/state/extract`,
      json(body),
    ),
  clearStoryState: (bookId: number, chapterNumber?: number | null) =>
    request<{ book_id: number; removed: number }>(
      `/api/books/${bookId}/state${chapterNumber ? `?chapter_number=${chapterNumber}` : ''}`,
      { method: 'DELETE' },
    ),

  // ---------------- 从零原创 ----------------
  createOriginalBook: (body: OriginalBookRequest) =>
    request<{ job_id: string; kind: string; message: string }>('/api/books/original', json(body)),
  planChapters: (bookId: number, body: PlanChaptersRequest) =>
    request<{ job_id: string; kind: string; message: string }>(
      `/api/books/${bookId}/plan-chapters`,
      json(body),
    ),
  planChaptersSync: (bookId: number, body: PlanChaptersRequest) =>
    request<{ book_id: number; count: number; planned: any[] }>(
      `/api/books/${bookId}/plan-chapters/sync`,
      json(body),
    ),

  // ---------------- 续写 ----------------
  previewContext: (body: GenerateRequest) =>
    request<GenerateContext>('/api/generate/preview', json(body)),
  generate: (body: GenerateRequest) => request<Generation>('/api/generate', json(body)),
  generateBatch: (body: BatchGenerateRequest) =>
    request<{ job_id: string; kind: string; message: string }>('/api/generate/batch', json(body)),

  listGenerations: (bookId?: number, limit = 30) =>
    request<Generation[]>(
      `/api/generations?limit=${limit}${bookId ? `&book_id=${bookId}` : ''}`,
    ),
  getGeneration: (id: number) => request<Generation>(`/api/generations/${id}`),
  deleteGeneration: (id: number) => request<void>(`/api/generations/${id}`, { method: 'DELETE' }),
  saveGeneration: (
    id: number,
    body: { title?: string; number?: number; replace_chapter_id?: number },
  ) => request<ChapterOut>(`/api/generations/${id}/save`, json(body)),
  consistencyCheck: (body: {
    book_id: number
    source_text: string
    model?: string | null
    provider_id?: number | null
  }) => request<ConsistencyResult>('/api/consistency-check', json(body)),

  // ---------------- 番茄 ----------------
  fanqieStatus: () => request<FanqieStatus>('/api/fanqie/status'),
  fanqieSelftest: () => request<FanqieSelftest>('/api/fanqie/selftest', json({})),
  fanqieSearch: (keyword: string) =>
    request<{ keyword: string; result: any }>('/api/fanqie/search', json({ keyword })),
  fanqieImport: (body: {
    book_id: string
    title?: string
    max_chapters?: number | null
    start_chapter?: number
    analyze?: boolean
  }) => request<{ job_id: string; message: string }>('/api/fanqie/import', json(body)),

  // ---------------- 设置 / 用量 / 提示词 ----------------
  getSettings: () => request<SettingsResponse>('/api/settings'),
  updateSettings: (body: Partial<AppSettings> & { api_key?: string | null }) =>
    request<SettingsResponse>('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  listModels: (providerId?: number | null) =>
    request<ModelInfo[]>(
      `/api/settings/models${providerId ? `?provider_id=${providerId}` : ''}`,
    ),
  validateModels: (providerId?: number | null) =>
    request<ModelValidation>(
      `/api/settings/validate${providerId ? `?provider_id=${providerId}` : ''}`,
    ),
  autofixModels: (providerId?: number | null) =>
    request<{ applied: Record<string, string>; settings: AppSettings; providers: Provider[] }>(
      `/api/settings/autofix-models${providerId ? `?provider_id=${providerId}` : ''}`,
      json({}),
    ),

  // ---------------- LLM 网关 ----------------
  listProviders: () => request<ProviderList>('/api/providers'),
  createProvider: (body: Partial<Provider> & { api_key?: string | null }) =>
    request<Provider>('/api/providers', json(body)),
  updateProvider: (id: number, body: Partial<Provider> & { api_key?: string | null }) =>
    request<Provider>(`/api/providers/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  activateProvider: (id: number) =>
    request<ProviderList>(`/api/providers/${id}/activate`, json({})),
  deleteProvider: (id: number) =>
    request<ProviderList>(`/api/providers/${id}`, { method: 'DELETE' }),
  getUsage: (limit = 20) => request<UsageSummary>(`/api/usage?limit=${limit}`),
  listPrompts: () => request<{ items: PromptItem[] }>('/api/prompts'),
  updatePrompt: (key: string, template: string) =>
    request<{ key: string; is_custom: boolean }>(`/api/prompts/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template }),
    }),
  resetPrompt: (key: string) =>
    request<{ key: string; is_custom: boolean }>(`/api/prompts/${key}`, { method: 'DELETE' }),
}

/**
 * 流式生成（SSE over POST）。
 * 用 fetch + ReadableStream 手动解析，因为 EventSource 不支持 POST。
 * 需要处理：一次读到多个事件、以及跨 chunk 被截断的半条消息。
 */
export async function* streamGenerate(
  body: GenerateRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch('/api/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    let detail = `生成失败（HTTP ${res.status}）`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* 忽略解析失败 */
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let index = buffer.indexOf('\n\n')
      while (index !== -1) {
        const rawEvent = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        const dataLine = rawEvent
          .split('\n')
          .map((line) => line.trim())
          .find((line) => line.startsWith('data:'))
        if (dataLine) {
          const payload = dataLine.slice(5).trim()
          if (payload === '[DONE]') return
          try {
            yield JSON.parse(payload) as StreamEvent
          } catch {
            /* 半条消息，忽略 */
          }
        }
        index = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}
