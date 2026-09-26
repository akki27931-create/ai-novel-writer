/** 续写页：RAG 上下文预览 + 流式生成 + 保存/润色/重写 + 生成历史。 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, streamGenerate } from '../api'
import JobProgress from '../components/JobProgress'
import Modal from '../components/Modal'
import PlainText from '../components/PlainText'
import { toast } from '../components/Toast'
import { formatCost, formatNumber, MODE_LABELS } from '../lib/format'
import type {
  AppSettings,
  Book,
  ChapterBrief,
  ConsistencyResult,
  GenerateContext,
  GenerateMode,
  GenerateRequest,
  Generation,
  RetrievedChunk,
  StoryState,
} from '../types'

const FALLBACK_SETTINGS: Pick<AppSettings, 'text_model' | 'reasoning_model'> = {
  // 故意留空：模型名必须以你的网关为准，这里不预设假名字，免得误导
  text_model: '',
  reasoning_model: '',
}

export default function Write() {
  const { bookId } = useParams()
  const id = Number(bookId)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const [book, setBook] = useState<Book | null>(null)
  const [chapters, setChapters] = useState<ChapterBrief[]>([])
  const [models, setModels] = useState(FALLBACK_SETTINGS)
  const [history, setHistory] = useState<Generation[]>([])

  // 表单
  const [baseChapter, setBaseChapter] = useState<number | null>(null)
  const [targetChapter, setTargetChapter] = useState<number>(1)
  const [autoGoal, setAutoGoal] = useState(true)   // 默认：让 AI 自己决定剧情
  const [goal, setGoal] = useState('')
  const [targetWords, setTargetWords] = useState(3000)
  const [mode, setMode] = useState<GenerateMode>('continue')
  const [modelChoice, setModelChoice] = useState<'auto' | 'text' | 'reasoning' | 'custom'>('auto')
  const [customModel, setCustomModel] = useState('')
  const [previousChapters, setPreviousChapters] = useState(2)
  const [autoSave, setAutoSave] = useState(false)
  const [sourceText, setSourceText] = useState('')

  // 生成状态
  const [output, setOutput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [status, setStatus] = useState('')
  const [meta, setMeta] = useState<{
    model: string
    prompt: string
    retrieved: RetrievedChunk[]
    storyState?: string
  } | null>(null)
  const [done, setDone] = useState<{ id: number; input: number; output: number; usd: number; cny: number; words: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // 上下文预览
  const [preview, setPreview] = useState<GenerateContext | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewBusy, setPreviewBusy] = useState(false)

  // 全自动：AI 拟定的计划
  const [planned, setPlanned] = useState<{ title: string; goal: string; plan: Record<string, unknown> | null } | null>(null)
  const [batchCount, setBatchCount] = useState(3)
  const [batchJobId, setBatchJobId] = useState<string | null>(null)

  // 检查结果
  const [consistency, setConsistency] = useState<ConsistencyResult | null>(null)

  // 剧情状态层（续写一致性的依据）
  const [story, setStory] = useState<StoryState | null>(null)
  const [storyBusy, setStoryBusy] = useState(false)
  const [storyJobId, setStoryJobId] = useState<string | null>(null)

  const refreshStory = useCallback(async () => {
    if (!id) return
    try {
      setStory(await api.getStoryState(id, targetChapter))
    } catch {
      setStory(null)
    }
  }, [id, targetChapter])

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [b, c, s, g] = await Promise.all([
        api.getBook(id),
        api.listChapters(id),
        api.getSettings(),
        api.listGenerations(id, 20),
      ])
      setBook(b)
      setChapters(c)
      setModels({
        text_model: s.settings.text_model,
        reasoning_model: s.settings.reasoning_model,
      })
      setHistory(g)

      const query = searchParams.get('chapter')
      const last = c.length > 0 ? c[c.length - 1].number : 0
      const fromQuery = query ? Number(query) : NaN
      const base = Number.isFinite(fromQuery) ? fromQuery : last
      setBaseChapter(base)
      setTargetChapter(base + 1)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }, [id, searchParams])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    refreshStory()
  }, [refreshStory])

  const resolvedModel = useMemo(() => {
    if (modelChoice === 'auto') return null
    if (modelChoice === 'text') return models.text_model
    if (modelChoice === 'reasoning') return models.reasoning_model
    return customModel.trim() || null
  }, [modelChoice, models, customModel])

  const buildRequest = useCallback(
    (overrideMode?: GenerateMode): GenerateRequest => ({
      book_id: id,
      chapter_number: targetChapter,
      goal: autoGoal ? '' : goal.trim(),
      target_words: targetWords,
      model: resolvedModel,
      mode: overrideMode ?? mode,
      source_text: sourceText,
      previous_chapters: previousChapters,
      auto_save: autoSave,
      auto_goal: autoGoal,
    }),
    [
      id,
      targetChapter,
      autoGoal,
      goal,
      targetWords,
      resolvedModel,
      mode,
      sourceText,
      previousChapters,
      autoSave,
    ],
  )

  const validate = (): boolean => {
    const writable = mode === 'continue' || mode === 'outline'
    if (writable && !autoGoal && !goal.trim()) {
      toast.error('请填写「本章目标」，或切换成「AI 自动决定剧情」')
      return false
    }
    if ((mode === 'polish' || mode === 'rewrite') && !sourceText.trim()) {
      toast.error('润色/重写模式需要先填写「待处理正文」')
      return false
    }
    return true
  }

  /** 一键连写 N 章：AI 全自动，每章自己推演剧情并自动入库。 */
  const startBatch = async () => {
    setBatchJobId(null)
    try {
      const res = await api.generateBatch({
        book_id: id,
        count: batchCount,
        target_words: targetWords,
        model: resolvedModel,
        previous_chapters: previousChapters,
        start_chapter: targetChapter,
      })
      toast.success(res.message)
      setBatchJobId(res.job_id)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const startGenerate = async (overrideMode?: GenerateMode) => {
    if (!validate()) return
    setOutput('')
    setMeta(null)
    setDone(null)
    setConsistency(null)
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      for await (const event of streamGenerate(buildRequest(overrideMode), controller.signal)) {
        if (event.type === 'meta') {
          setMeta({
            model: event.model,
            prompt: event.prompt,
            retrieved: event.retrieved,
            storyState: event.story_state,
          })
          if (event.auto_planned) {
            setPlanned({
              title: event.title ?? '',
              goal: event.goal ?? '',
              plan: event.plan ?? null,
            })
          }
        } else if (event.type === 'status') {
          setStatus(event.message)
          toast.info(event.message)
        } else if (event.type === 'delta') {
          setOutput((prev) => prev + event.text)
        } else if (event.type === 'done') {
          setDone({
            id: event.generation_id,
            input: event.input_tokens,
            output: event.output_tokens,
            usd: event.cost_usd,
            cny: event.cost_cny,
            words: event.word_count,
          })
          if (event.truncated) {
            toast.error(
              '输出达到 max_tokens 上限被截断，本章可能不完整。请到「设置 → 生成参数」把 max_tokens 调大（建议 32768）。',
            )
          } else if (event.warning) {
            toast.info(event.warning)
          }
          api.listGenerations(id, 20).then(setHistory).catch(() => undefined)
          if (event.saved_chapter_id) refreshStory()
        } else if (event.type === 'error') {
          toast.error(event.message)
        }
      }
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        toast.error((error as Error).message)
      }
    } finally {
      setStreaming(false)
      setStatus('')
      abortRef.current = null
    }
  }

  /** 给已有书稿批量补建剧情状态层（走廉价模型）。 */
  const startExtractStates = async () => {
    setStoryBusy(true)
    try {
      const res = await api.extractStates(id, {
        upto: targetChapter,
        batch_size: 4,
      })
      setStoryJobId(res.job_id)
      toast.success(res.message)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setStoryBusy(false)
    }
  }

  const stop = () => {
    abortRef.current?.abort()
    setStreaming(false)
  }

  const doPreview = async () => {
    if (!validate()) return
    setPreviewBusy(true)
    try {
      setPreview(await api.previewContext(buildRequest()))
      setPreviewOpen(true)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setPreviewBusy(false)
    }
  }

  const copyOutput = async () => {
    try {
      await navigator.clipboard.writeText(output)
      toast.success('已复制到剪贴板')
    } catch {
      toast.error('复制失败，请手动选择文本复制')
    }
  }

  const saveOutput = async (generationId: number) => {
    try {
      const chapter = await api.saveGeneration(generationId, {
        number: targetChapter,
        title: `第${targetChapter}章`,
      })
      toast.success(`已保存为第 ${chapter.number} 章`)
      load()
    } catch (error) {
      const message = (error as Error).message
      if (message.includes('已存在')) {
        const existing = chapters.find((c) => c.number === targetChapter)
        if (existing && window.confirm(`${message}\n\n是否覆盖原内容？`)) {
          try {
            await api.saveGeneration(generationId, {
              number: targetChapter,
              title: `第${targetChapter}章`,
              replace_chapter_id: existing.id,
            })
            toast.success('已覆盖保存')
            load()
          } catch (inner) {
            toast.error((inner as Error).message)
          }
        }
        return
      }
      toast.error(message)
    }
  }

  const checkConsistency = async (text: string) => {
    if (!text.trim()) {
      toast.error('没有可检查的正文')
      return
    }
    try {
      setConsistency(await api.consistencyCheck({ book_id: id, source_text: text }))
      toast.success('检查完成')
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  if (!book) return <div className="card text-sm text-slate-500">加载中…</div>

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <button className="mb-1 text-xs text-slate-500 hover:text-slate-300" onClick={() => navigate(`/books/${id}`)}>
            ← 返回《{book.title}》
          </button>
          <h2 className="text-xl font-semibold text-slate-100">AI 续写</h2>
          <p className="mt-1 text-xs text-slate-500">
            仅发送：总纲 + 相关人物卡 + 相关世界观 + 未回收伏笔 + 最近 {previousChapters} 章原文 + 本章目标。检索会排除目标章节及之后内容。
          </p>
        </div>
        <Link0 to={`/books/${id}`} />
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* -------- 左：表单 -------- */}
        <div className="space-y-4">
          <div className="card space-y-3">
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="label">续写起点（从这一章之后写）</label>
                <select
                  className="input"
                  value={baseChapter ?? 0}
                  onChange={(e) => {
                    const value = Number(e.target.value)
                    setBaseChapter(value)
                    setTargetChapter(value + 1)
                  }}
                >
                  <option value={0}>— 从头开始 —</option>
                  {chapters.map((chapter) => (
                    <option key={chapter.id} value={chapter.number}>
                      第 {chapter.number} 章 {chapter.title}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">目标章节号</label>
                <input
                  type="number"
                  min={1}
                  className="input"
                  value={targetChapter}
                  onChange={(e) => setTargetChapter(Number(e.target.value) || 1)}
                />
              </div>
              <div>
                <label className="label">字数要求</label>
                <input
                  type="number"
                  min={200}
                  step={100}
                  className="input"
                  value={targetWords}
                  onChange={(e) => setTargetWords(Number(e.target.value) || 3000)}
                />
              </div>
            </div>

            <div>
              <label className="label">这一章写什么？</label>
              <div className="flex gap-2">
                <button
                  className={`btn flex-1 ${
                    autoGoal
                      ? 'bg-indigo-600 text-white'
                      : 'border border-slate-700 text-slate-300 hover:bg-slate-800'
                  }`}
                  onClick={() => setAutoGoal(true)}
                >
                  🤖 AI 自动决定剧情
                </button>
                <button
                  className={`btn flex-1 ${
                    !autoGoal
                      ? 'bg-indigo-600 text-white'
                      : 'border border-slate-700 text-slate-300 hover:bg-slate-800'
                  }`}
                  onClick={() => setAutoGoal(false)}
                >
                  ✍️ 我自己指定目标
                </button>
              </div>

              {autoGoal ? (
                <p className="mt-2 rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
                  AI 会读取<strong className="text-slate-300">总纲、未回收伏笔、最近几章摘要和上一章结尾</strong>，
                  自己决定这一章写什么、顺手起好章节名。你什么都不用填，直接点下面的按钮。
                </p>
              ) : (
                <textarea
                  className="input mt-2 h-24 resize-y"
                  placeholder="例如：主角在拍卖会上抢到残破玉简，遭到三大世家围攻，凭借新悟的剑意反杀，末尾引出玉简中的上古残魂。"
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                />
              )}
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="label">生成模式</label>
                <select className="input" value={mode} onChange={(e) => setMode(e.target.value as GenerateMode)}>
                  {(Object.keys(MODE_LABELS) as GenerateMode[]).map((key) => (
                    <option key={key} value={key}>
                      {MODE_LABELS[key]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">模型</label>
                <select
                  className="input"
                  value={modelChoice}
                  onChange={(e) => setModelChoice(e.target.value as typeof modelChoice)}
                >
                  <option value="auto">自动（大纲→复杂模型，其余→正文模型）</option>
                  <option value="text">正文模型：{models.text_model}</option>
                  <option value="reasoning">复杂模型：{models.reasoning_model}</option>
                  <option value="custom">自定义…</option>
                </select>
              </div>
              <div>
                <label className="label">携带最近章节数</label>
                <input
                  type="number"
                  min={1}
                  max={3}
                  className="input"
                  value={previousChapters}
                  onChange={(e) => setPreviousChapters(Number(e.target.value) || 2)}
                />
              </div>
            </div>

            {modelChoice === 'custom' ? (
              <div>
                <label className="label">自定义模型名</label>
                <input className="input" value={customModel} onChange={(e) => setCustomModel(e.target.value)} />
              </div>
            ) : null}

            {mode === 'polish' || mode === 'rewrite' ? (
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="label !mb-0">待处理正文</label>
                  <select
                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300"
                    value=""
                    onChange={async (e) => {
                      const chapterId = Number(e.target.value)
                      if (!chapterId) return
                      try {
                        const chapter = await api.getChapter(chapterId)
                        setSourceText(chapter.content)
                        toast.success(`已填入第 ${chapter.number} 章`)
                      } catch (error) {
                        toast.error((error as Error).message)
                      }
                    }}
                  >
                    <option value="">从章节填入…</option>
                    {chapters.map((chapter) => (
                      <option key={chapter.id} value={chapter.id}>
                        第 {chapter.number} 章
                      </option>
                    ))}
                  </select>
                </div>
                <textarea
                  className="input h-40 resize-y font-mono text-xs"
                  value={sourceText}
                  onChange={(e) => setSourceText(e.target.value)}
                />
              </div>
            ) : null}

            <label className="flex items-center gap-2 text-sm text-slate-400">
              <input type="checkbox" checked={autoSave} onChange={(e) => setAutoSave(e.target.checked)} />
              生成成功后自动保存为章节（章节号冲突时自动顺延）
            </label>

            <div className="flex flex-wrap gap-2">
              <button className="btn-primary" onClick={() => startGenerate()} disabled={streaming}>
                {streaming ? '生成中…' : autoGoal ? '🤖 生成下一章' : '开始生成'}
              </button>
              <button className="btn-ghost" onClick={doPreview} disabled={previewBusy}>
                {previewBusy ? '构建中…' : '预览上下文'}
              </button>
              {streaming ? (
                <button className="btn-danger" onClick={stop}>
                  停止
                </button>
              ) : null}
            </div>

            {mode === 'continue' ? (
              <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                <p className="text-xs text-slate-400">
                  <strong className="text-slate-300">懒得一章一章点？</strong>
                  让 AI 一口气连着写，每章自己推演剧情、写完自动进目录。
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-500">连写</span>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    className="input !w-20"
                    value={batchCount}
                    onChange={(e) => setBatchCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                  />
                  <span className="text-xs text-slate-500">章</span>
                  <button className="btn-primary" onClick={startBatch} disabled={streaming || batchJobId !== null}>
                    🚀 一键连写 {batchCount} 章
                  </button>
                  <span className="text-[11px] text-slate-500">
                    从第 {targetChapter} 章开始，约 {batchCount * targetWords} 字
                  </span>
                </div>
                {batchJobId ? (
                  <JobProgress
                    jobId={batchJobId}
                    onSettled={(job) => {
                      if (job.status === 'success') {
                        toast.success('连写完成，已自动存入目录')
                        load()
                      }
                    }}
                  />
                ) : null}
              </div>
            ) : null}
          </div>

          {/* -------- 结果 -------- */}
          <div className="card space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-slate-300">
                {MODE_LABELS[mode]}结果
                {meta ? <span className="ml-2 text-[11px] font-normal text-slate-500">模型 {meta.model}</span> : null}
              </h3>
              {done ? (
                <span className="text-[11px] text-slate-500">
                  {formatNumber(done.words)} 字 · in {formatNumber(done.input)} / out{' '}
                  {formatNumber(done.output)} tokens · 预估 {formatCost(done.usd, done.cny)}
                </span>
              ) : null}
            </div>

            {planned ? (
              <div className="rounded-lg border border-indigo-900/60 bg-indigo-950/30 p-3 text-xs">
                <p className="text-indigo-200">
                  🤖 AI 自己定的：第 {targetChapter} 章
                  {planned.title ? `《${planned.title}》` : ''}
                </p>
                <p className="mt-1 text-slate-400">本章目标：{planned.goal}</p>
                {String(planned.plan?.hook ?? '') ? (
                  <p className="mt-1 text-slate-500">
                    结尾钩子：{String(planned.plan?.hook ?? '')}
                  </p>
                ) : null}
              </div>
            ) : null}

            {output ? (
              <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/40 p-4">
                <PlainText text={output} />
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                {streaming ? '正在生成，请稍候…' : '还没有内容。填好本章目标后点「开始生成」。'}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <button className="btn-ghost" onClick={copyOutput} disabled={!output}>
                复制正文
              </button>
              <button
                className="btn-ghost"
                onClick={() => {
                  setSourceText(output)
                  setMode('polish')
                  setGoal(goal || '在不改变剧情的前提下润色本章')
                  toast.info('已切到润色模式，点「开始生成」即可润色')
                }}
                disabled={!output}
              >
                一键润色
              </button>
              <button
                className="btn-ghost"
                onClick={() => {
                  setSourceText(output)
                  setMode('rewrite')
                  toast.info('已切到重写模式，点「开始生成」即可重写')
                }}
                disabled={!output}
              >
                重写
              </button>
              <button className="btn-primary" onClick={() => saveOutput(done?.id ?? 0)} disabled={!done}>
                保存为章节
              </button>
              <button className="btn-ghost" onClick={() => checkConsistency(output)} disabled={!output}>
                检查伏笔 / 矛盾
              </button>
            </div>

            {consistency ? (
              <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                <p className="text-xs text-slate-400">总体：{consistency.report.overall}</p>
                {consistency.report.issues.length === 0 ? (
                  <p className="text-xs text-emerald-400">未发现明显问题</p>
                ) : (
                  consistency.report.issues.map((issue, index) => (
                    <div key={index} className="rounded border border-slate-800 p-2 text-xs">
                      <div className="mb-1 flex items-center gap-2">
                        <span
                          className={`tag ${
                            issue.severity === '高'
                              ? 'bg-rose-950 text-rose-300'
                              : issue.severity === '中'
                                ? 'bg-amber-950 text-amber-300'
                                : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {issue.severity} · {issue.type}
                        </span>
                      </div>
                      <p className="text-slate-300">{issue.detail}</p>
                      {issue.suggestion ? (
                        <p className="mt-1 text-slate-500">建议：{issue.suggestion}</p>
                      ) : null}
                    </div>
                  ))
                )}
              </div>
            ) : null}
          </div>
        </div>

        {/* -------- 右：历史 + 检索 -------- */}
        <aside className="space-y-4">
          {/* -------- 剧情状态：续写一致性的依据 -------- */}
          <div className="card space-y-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-slate-300">剧情状态</h3>
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={startExtractStates}
                disabled={storyBusy}
              >
                {storyBusy ? '启动中…' : '补建状态'}
              </button>
            </div>

            {story ? (
              <>
                <p className="text-[11px] text-slate-500">
                  已建状态 {story.chapters.length} 章
                  {story.missing_chapters.length > 0
                    ? ` ｜ 待补建 ${story.missing_chapters.length} 章`
                    : ' ｜ 全部已覆盖'}
                  {story.state_model ? ` ｜ 抽取模型 ${story.state_model}` : ''}
                  {story.auto_extract ? ' ｜ 写完后自动抽取' : ' ｜ 自动抽取已关闭'}
                </p>

                {story.missing_chapters.length > 0 ? (
                  <p className="rounded border border-amber-900 bg-amber-950/30 p-2 text-[11px] text-amber-300">
                    第 {story.missing_chapters.slice(0, 12).join('、')}
                    {story.missing_chapters.length > 12 ? ' …' : ''} 章还没有状态，
                    续写时这些章节的信息只能靠原文片段检索，建议点「补建状态」。
                  </p>
                ) : null}

                {story.characters.length > 0 ? (
                  <div className="max-h-56 space-y-1 overflow-y-auto">
                    {story.characters.slice(0, 12).map((c) => (
                      <div
                        key={c.id}
                        className="rounded border border-slate-800 bg-slate-950/40 p-2 text-[11px]"
                      >
                        <div className="flex items-center justify-between text-slate-400">
                          <span className="font-medium text-slate-300">{c.name}</span>
                          <span>{c.last_seen_chapter ? `第${c.last_seen_chapter}章` : '未出场'}</span>
                        </div>
                        <p className="text-slate-500">
                          {[c.location && `位置：${c.location}`, c.doing && `在做：${c.doing}`]
                            .filter(Boolean)
                            .join(' ｜ ') || '（暂无状态）'}
                        </p>
                        {c.items && c.items.length > 0 ? (
                          <p className="text-slate-500">持有：{c.items.join('、')}</p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}

                {story.snapshot ? (
                  <PreviewBlock title="实际注入模型的状态快照" text={story.snapshot} />
                ) : null}
              </>
            ) : (
              <p className="text-xs text-slate-500">暂时读不到状态层，请确认后端已启动。</p>
            )}

            {storyJobId ? (
              <JobProgress
                jobId={storyJobId}
                onDone={() => {
                  setStoryJobId(null)
                  refreshStory()
                }}
              />
            ) : null}
          </div>

          <div className="card space-y-2">
            <h3 className="text-sm font-semibold text-slate-300">本次检索到的片段</h3>
            {meta?.retrieved && meta.retrieved.length > 0 ? (
              meta.retrieved.map((item, index) => (
                <div key={index} className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs">
                  <div className="mb-1 flex justify-between text-slate-500">
                    <span>
                      第{item.chapter_number}章 {item.chapter_title}
                      {item.recall === 'keyword' ? (
                        <span className="ml-1 text-amber-300" title="关键词必召回（与人物/物品直接相关）">
                          ★关键锚点
                        </span>
                      ) : null}
                    </span>
                    <span>相似度 {item.score.toFixed(3)}</span>
                  </div>
                  <p className="line-clamp-3 text-slate-400">{item.text}</p>
                </div>
              ))
            ) : (
              <p className="text-xs text-slate-500">生成后会显示本次命中的原文片段。</p>
            )}
          </div>

          <div className="card space-y-2">
            <h3 className="text-sm font-semibold text-slate-300">生成历史</h3>
            {history.length === 0 ? (
              <p className="text-xs text-slate-500">暂无记录。</p>
            ) : (
              <div className="max-h-[50vh] space-y-2 overflow-y-auto">
                {history.map((item) => (
                  <div key={item.id} className="rounded border border-slate-800 bg-slate-950/40 p-2 text-xs">
                    <div className="mb-1 flex items-center justify-between text-slate-500">
                      <span>
                        第{item.chapter_number}章 · {MODE_LABELS[item.mode] ?? item.mode}
                      </span>
                      <span>{item.status === 'success' ? '成功' : '失败'}</span>
                    </div>
                    <p className="line-clamp-3 text-slate-400">{item.result || item.error}</p>
                    <div className="mt-1 flex gap-2">
                      <button
                        className="text-indigo-400 hover:text-indigo-300"
                        onClick={() => {
                          setOutput(item.result)
                          setDone({
                            id: item.id,
                            input: item.input_tokens,
                            output: item.output_tokens,
                            usd: item.cost_usd,
                            cny: item.cost_cny,
                            words: 0,
                          })
                          setTargetChapter(item.chapter_number)
                        }}
                      >
                        载入
                      </button>
                      <button
                        className="text-rose-400 hover:text-rose-300"
                        onClick={async () => {
                          try {
                            await api.deleteGeneration(item.id)
                            setHistory(await api.listGenerations(id, 20))
                          } catch (error) {
                            toast.error((error as Error).message)
                          }
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>

      <Modal open={previewOpen} title="本次将发送给模型的内容" onClose={() => setPreviewOpen(false)} wide>
        {preview ? (
          <div className="space-y-4 text-xs">
            <div className="grid gap-2 text-slate-400 md:grid-cols-3">
              <div>目标章节：第 {preview.chapter_number} 章</div>
              <div>使用模型：{preview.model || '（未配置，请到设置页拉取可用模型）'}</div>
              <div>检索片段：{preview.retrieved.length} 条</div>
            </div>
            <PreviewBlock title="最终提示词（prompt）" text={preview.prompt} mono />
            <PreviewBlock
              title="★ 当前剧情状态（人物位置 / 在做什么 / 持有物 / 谁不在场）"
              text={preview.story_state}
            />
            <PreviewBlock title="大纲" text={preview.outline_text} />
            <PreviewBlock title="核心设定（人物卡 / 世界观 / 文风 / 原文片段）" text={preview.core_settings_text} />
            <PreviewBlock title="未回收伏笔" text={preview.foreshadow_text} />
            <PreviewBlock title="上一章内容" text={preview.previous_tail} />
          </div>
        ) : null}
      </Modal>
    </div>
  )
}

function PreviewBlock({ title, text, mono }: { title: string; text: string; mono?: boolean }) {
  const [open, setOpen] = useState(title.includes('提示词') || title.includes('剧情状态'))
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50">
      <button
        className="flex w-full items-center justify-between px-3 py-2 text-left text-slate-300"
        onClick={() => setOpen((v) => !v)}
      >
        <span>{title}</span>
        <span className="text-slate-500">{open ? '收起' : '展开'}</span>
      </button>
      {open ? (
        <pre
          className={`max-h-72 overflow-auto whitespace-pre-wrap border-t border-slate-800 px-3 py-2 text-slate-400 ${
            mono ? 'font-mono' : ''
          }`}
        >
          {text || '（空）'}
        </pre>
      ) : null}
    </div>
  )
}

/** 右上角返回书籍页的小按钮（抽出来避免 header 里写太多 JSX）。 */
function Link0({ to }: { to: string }) {
  const navigate = useNavigate()
  return (
    <button className="btn-ghost" onClick={() => navigate(to)}>
      书籍详情
    </button>
  )
}
