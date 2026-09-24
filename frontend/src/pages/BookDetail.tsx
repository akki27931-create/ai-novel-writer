/** 书籍页：目录 / 阅读 / 拆书结果（可编辑），并触发拆书与向量化后台任务。 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import JobProgress from '../components/JobProgress'
import Modal from '../components/Modal'
import PlainText from '../components/PlainText'
import { toast } from '../components/Toast'
import { formatNumber, formatWords, sourceLabel, SECTION_LABELS } from '../lib/format'
import type { Analysis, AnalyzeSection, Book, BookStats, ChapterBrief, ChapterOut } from '../types'

type Tab = 'catalog' | 'read' | 'analysis'

const ANALYSIS_TABS: { key: AnalyzeSection; label: string; json: boolean }[] = [
  { key: 'outline', label: '总纲', json: false },
  { key: 'characters', label: '人物卡', json: true },
  { key: 'worldview', label: '世界观', json: true },
  { key: 'timeline', label: '时间线', json: true },
  { key: 'foreshadows', label: '伏笔', json: true },
  { key: 'style', label: '文风', json: true },
]

export default function BookDetail() {
  const { bookId } = useParams()
  const id = Number(bookId)
  const navigate = useNavigate()

  const [book, setBook] = useState<Book | null>(null)
  const [stats, setStats] = useState<BookStats | null>(null)
  const [chapters, setChapters] = useState<ChapterBrief[]>([])
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [tab, setTab] = useState<Tab>('catalog')
  const [current, setCurrent] = useState<ChapterOut | null>(null)
  const [loadingContent, setLoadingContent] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [analyzeOpen, setAnalyzeOpen] = useState(false)
  const [autoCount, setAutoCount] = useState(5)

  /** 一键全自动续写：AI 自己决定剧情和标题，写完直接进目录。 */
  const startAutoWrite = async (count: number) => {
    try {
      const res = await api.generateBatch({
        book_id: id,
        count,
        target_words: 3000,
        previous_chapters: 2,
      })
      toast.success(res.message)
      setJobId(res.job_id)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const reload = useCallback(async () => {
    if (!id) return
    try {
      const [b, s, c, a] = await Promise.all([
        api.getBook(id),
        api.bookStats(id),
        api.listChapters(id),
        api.getAnalysis(id),
      ])
      setBook(b)
      setStats(s)
      setChapters(c)
      setAnalysis(a)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }, [id])

  useEffect(() => {
    reload()
  }, [reload])

  const openChapter = async (chapterId: number) => {
    setTab('read')
    setLoadingContent(true)
    try {
      setCurrent(await api.getChapter(chapterId))
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setLoadingContent(false)
    }
  }

  if (!book) {
    return <div className="card text-sm text-slate-500">加载中…</div>
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <button className="mb-1 text-xs text-slate-500 hover:text-slate-300" onClick={() => navigate('/')}>
            ← 返回书架
          </button>
          <h2 className="text-xl font-semibold text-slate-100">《{book.title}》</h2>
          <p className="mt-1 text-xs text-slate-500">
            {book.author || '佚名'} · {sourceLabel(book.source)}
            {stats
              ? ` · ${formatNumber(stats.chapters)} 章 · ${formatWords(stats.words)} · 已向量化 ${stats.vectorized}/${stats.chapters} · 生成 ${stats.generations} 次`
              : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={() => startAutoWrite(1)} disabled={jobId !== null}>
            🤖 一键续写下一章
          </button>
          <div className="flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-1">
            <span className="text-xs text-slate-400">连写</span>
            <input
              type="number"
              min={1}
              max={20}
              className="input !w-16 !py-1 text-center"
              value={autoCount}
              onChange={(e) => setAutoCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
            />
            <span className="text-xs text-slate-400">章</span>
            <button
              className="btn-ghost !px-2 !py-1 text-xs"
              onClick={() => startAutoWrite(autoCount)}
              disabled={jobId !== null}
            >
              开始
            </button>
          </div>
          <Link className="btn-ghost" to={`/books/${id}/write`}>
            手动续写
          </Link>
          <button className="btn-ghost" onClick={() => setAnalyzeOpen(true)}>
            重新拆书
          </button>
          <button
            className="btn-ghost"
            onClick={async () => {
              try {
                const res = await api.startVectorize(id, { force: false })
                setJobId(res.job_id)
                toast.info(res.message)
              } catch (error) {
                toast.error((error as Error).message)
              }
            }}
          >
            建立向量索引
          </button>
        </div>
      </header>

      {jobId ? (
        <JobProgress
          jobId={jobId}
          onSettled={(job) => {
            reload()
            if (job.kind === 'auto-write' && job.status === 'success') {
              toast.success('AI 已写完并自动存进目录，往下翻就能看到新章节')
            }
          }}
        />
      ) : null}

      <div className="flex gap-2 border-b border-slate-800 pb-2">
        {(
          [
            ['catalog', `目录（${chapters.length}）`],
            ['read', '阅读'],
            ['analysis', '拆书结果'],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              tab === key ? 'bg-indigo-600/20 text-indigo-200' : 'text-slate-400 hover:bg-slate-800'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'catalog' ? (
        <section className="space-y-1">
          {chapters.length === 0 ? (
            <div className="card text-sm text-slate-500">还没有章节。</div>
          ) : (
            chapters.map((chapter) => (
              <div
                key={chapter.id}
                className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-slate-900"
              >
                <button
                  className="min-w-0 flex-1 text-left text-sm text-slate-300 hover:text-indigo-300"
                  onClick={() => openChapter(chapter.id)}
                >
                  <span className="mr-2 text-slate-500">{chapter.number}.</span>
                  {chapter.title}
                </button>
                <div className="flex shrink-0 items-center gap-2 text-[11px]">
                  <span className="text-slate-500">{formatNumber(chapter.word_count)}字</span>
                  <span className={`tag ${chapter.is_original ? 'bg-slate-800 text-slate-400' : 'bg-indigo-950 text-indigo-300'}`}>
                    {chapter.is_original ? '原文' : 'AI'}
                  </span>
                  {chapter.vectorized ? (
                    <span className="tag bg-emerald-950 text-emerald-400">已索引</span>
                  ) : null}
                  <Link
                    className="text-indigo-400 hover:text-indigo-300"
                    to={`/books/${id}/write?chapter=${chapter.number}`}
                  >
                    续写此章
                  </Link>
                </div>
              </div>
            ))
          )}
        </section>
      ) : null}

      {tab === 'read' ? (
        <section className="flex gap-4">
          <div className="w-64 shrink-0 space-y-1 overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/60 p-2" style={{ maxHeight: '70vh' }}>
            {chapters.map((chapter) => (
              <button
                key={chapter.id}
                onClick={() => openChapter(chapter.id)}
                className={`block w-full truncate rounded px-2 py-1.5 text-left text-xs ${
                  current?.id === chapter.id
                    ? 'bg-indigo-600/20 text-indigo-200'
                    : 'text-slate-400 hover:bg-slate-800'
                }`}
              >
                {chapter.number}. {chapter.title}
              </button>
            ))}
          </div>
          <div className="min-w-0 flex-1">
            {loadingContent ? (
              <div className="card text-sm text-slate-500">加载中…</div>
            ) : current ? (
              <ChapterReader
                chapter={current}
                onSaved={reload}
                onContinue={() => navigate(`/books/${id}/write?chapter=${current.number}`)}
              />
            ) : (
              <div className="card text-sm text-slate-500">从左侧选择一章开始阅读。</div>
            )}
          </div>
        </section>
      ) : null}

      {tab === 'analysis' ? (
        <AnalysisPanel
          bookId={id}
          analysis={analysis}
          onChanged={reload}
          onJob={setJobId}
          onOpenAnalyze={() => setAnalyzeOpen(true)}
        />
      ) : null}

      <AnalyzeModal
        open={analyzeOpen}
        bookId={id}
        onClose={() => setAnalyzeOpen(false)}
        onStarted={(newJobId) => {
          setJobId(newJobId)
          setAnalyzeOpen(false)
        }}
      />
    </div>
  )
}

// ======================================================================
function ChapterReader({
  chapter,
  onSaved,
  onContinue,
}: {
  chapter: ChapterOut
  onSaved: () => void
  onContinue: () => void
}) {
  const [content, setContent] = useState(chapter.content)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setContent(chapter.content)
    setEditing(false)
  }, [chapter.id, chapter.content])

  const save = async () => {
    setBusy(true)
    try {
      await api.updateChapter(chapter.id, { content })
      toast.success('已保存，并重建了本章向量索引')
      setEditing(false)
      onSaved()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-100">
          第{chapter.number}章 {chapter.title}
        </h3>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">{formatNumber(chapter.word_count)} 字</span>
          <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setEditing((v) => !v)}>
            {editing ? '取消编辑' : '编辑'}
          </button>
          {editing ? (
            <button className="btn-primary !px-2 !py-1 text-xs" onClick={save} disabled={busy}>
              保存
            </button>
          ) : null}
        </div>
      </div>

      {chapter.summary ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          摘要：{chapter.summary}
        </p>
      ) : null}

      {editing ? (
        <textarea
          className="input h-[60vh] resize-y font-mono text-sm"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      ) : (
        <PlainText text={chapter.content} />
      )}

      <div className="flex justify-end">
        <button className="btn-primary" onClick={onContinue}>
          基于本章续写
        </button>
      </div>
    </div>
  )
}

// ======================================================================
function AnalysisPanel({
  bookId,
  analysis,
  onChanged,
  onJob,
  onOpenAnalyze,
}: {
  bookId: number
  analysis: Analysis | null
  onChanged: () => void
  onJob: (id: string) => void
  onOpenAnalyze: () => void
}) {
  const [section, setSection] = useState<AnalyzeSection>('outline')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  const value = useMemo(() => {
    if (!analysis) return ''
    if (section === 'outline') return analysis.synopsis || ''
    const raw = (analysis as any)[section]
    return JSON.stringify(raw ?? (section === 'style' ? {} : []), null, 2)
  }, [analysis, section])

  useEffect(() => {
    setDraft(value)
  }, [value])

  const isEmpty =
    !analysis ||
    (section === 'outline'
      ? !analysis.synopsis
      : Array.isArray((analysis as any)[section])
        ? ((analysis as any)[section] as any[]).length === 0
        : !(analysis as any)[section] || Object.keys((analysis as any)[section] || {}).length === 0)

  const save = async () => {
    setBusy(true)
    try {
      const body: Record<string, unknown> = {}
      if (section === 'outline') {
        body.synopsis = draft
      } else {
        body[section] = JSON.parse(draft)
      }
      await api.updateAnalysis(bookId, body)
      toast.success('已保存')
      onChanged()
    } catch (error) {
      toast.error(
        error instanceof SyntaxError ? `JSON 格式错误：${error.message}` : (error as Error).message,
      )
    } finally {
      setBusy(false)
    }
  }

  const rerun = async () => {
    try {
      const res = await api.startAnalyze(bookId, {
        batch_size: 8,
        redo_summaries: false,
        section,
      })
      toast.info(res.message)
      onJob(res.job_id)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {ANALYSIS_TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setSection(item.key)}
            className={`rounded-lg px-3 py-1.5 text-xs ${
              section === item.key ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800'
            }`}
          >
            {item.label}
          </button>
        ))}
        {analysis?.updated_at ? (
          <span className="ml-auto text-[11px] text-slate-500">
            拆书结果更新时间 {new Date(analysis.updated_at).toLocaleString('zh-CN')}
          </span>
        ) : null}
      </div>

      {isEmpty ? (
        <div className="card space-y-2 text-sm text-slate-400">
          <p>「{SECTION_LABELS[section]}」还没有内容。</p>
          <div className="flex gap-2">
            <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={onOpenAnalyze}>
              执行拆书
            </button>
            <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={rerun}>
              单独重跑本板块
            </button>
          </div>
        </div>
      ) : (
        <>
          <textarea
            className="input h-[45vh] resize-y font-mono text-xs leading-6"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <button className="btn-primary" onClick={save} disabled={busy}>
              保存修改
            </button>
            <button className="btn-ghost" onClick={() => setDraft(value)}>
              撤销
            </button>
            <button className="btn-ghost" onClick={rerun}>
              单独重跑本板块
            </button>
            {section === 'outline' ? (
              <span className="text-[11px] text-slate-500">总纲为纯文本，直接编辑即可</span>
            ) : (
              <span className="text-[11px] text-slate-500">JSON 格式，保存前会做解析校验</span>
            )}
          </div>
        </>
      )}
    </section>
  )
}

// ======================================================================
function AnalyzeModal({
  open,
  bookId,
  onClose,
  onStarted,
}: {
  open: boolean
  bookId: number
  onClose: () => void
  onStarted: (jobId: string) => void
}) {
  const [model, setModel] = useState('')
  const [batchSize, setBatchSize] = useState(5)
  const [maxChapters, setMaxChapters] = useState<number | ''>('')
  const [redo, setRedo] = useState(false)
  const [section, setSection] = useState<AnalyzeSection>('all')
  const [busy, setBusy] = useState(false)

  const start = async () => {
    setBusy(true)
    try {
      const res = await api.startAnalyze(bookId, {
        model: model.trim() || null,
        batch_size: batchSize,
        max_chapters: maxChapters === '' ? null : Number(maxChapters),
        redo_summaries: redo,
        section,
      })
      toast.info(res.message)
      onStarted(res.job_id)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title="拆书设置"
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={start} disabled={busy}>
            {busy ? '启动中…' : '开始拆书'}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          流程：章节分块向量化 → 分批生成章节摘要（用正文模型）→ 全局拆书（用复杂任务模型）。
          已生成的章节摘要会复用，不会重复计费。
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">只拆前 N 章（留空=全部，省钱用）</label>
            <input
              type="number"
              min={1}
              className="input"
              value={maxChapters}
              onChange={(e) => setMaxChapters(e.target.value === '' ? '' : Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">每批章节数</label>
            <input
              type="number"
              min={1}
              max={30}
              className="input"
              value={batchSize}
              onChange={(e) => setBatchSize(Number(e.target.value) || 8)}
            />
          </div>
        </div>
        <div>
          <label className="label">拆书范围</label>
          <select className="input" value={section} onChange={(e) => setSection(e.target.value as AnalyzeSection)}>
            <option value="all">全部（推荐首次执行）</option>
            <option value="characters">只重跑人物卡</option>
            <option value="worldview">只重跑世界观</option>
            <option value="timeline">只重跑时间线</option>
            <option value="foreshadows">只重跑伏笔</option>
            <option value="style">只重跑文风</option>
            <option value="outline">只重跑大纲</option>
          </select>
        </div>
        <div>
          <label className="label">模型（留空=按设置里的复杂任务模型）</label>
          <input
            className="input"
            placeholder="留空即可，不要手填猜测的模型名"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
          <p className="mt-1 text-[11px] text-slate-500">
            只有当你想临时换模型时才填。填错会直接报「模型名不存在」，留空最安全。
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-400">
          <input type="checkbox" checked={redo} onChange={(e) => setRedo(e.target.checked)} />
          强制重新生成全部章节摘要
        </label>
      </div>
    </Modal>
  )
}
