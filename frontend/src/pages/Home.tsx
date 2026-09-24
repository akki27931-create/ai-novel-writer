/** 首页：导入小说（番茄 MCP / 上传 TXT-EPUB / 手动粘贴）+ 书架列表。 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import JobProgress from '../components/JobProgress'
import Modal from '../components/Modal'
import { toast } from '../components/Toast'
import { formatNumber, formatDate, sourceLabel } from '../lib/format'
import type { Book, FanqieStatus } from '../types'

type Tab = 'fanqie' | 'upload' | 'paste'

/** 从番茄返回的任意结构里猜书籍 ID */
function pickBookId(item: any): string {
  if (!item || typeof item !== 'object') return ''
  for (const key of ['book_id', 'bookId', 'id', 'bookid']) {
    if (item[key] !== undefined && item[key] !== null) return String(item[key])
  }
  return ''
}

function pickTitle(item: any): string {
  if (!item || typeof item !== 'object') return ''
  for (const key of ['title', 'book_name', 'name', 'bookName']) {
    if (item[key]) return String(item[key])
  }
  return ''
}

export default function Home() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('upload')
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)

  const loadBooks = useCallback(async () => {
    setLoading(true)
    try {
      setBooks(await api.listBooks())
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadBooks()
  }, [loadBooks])

  const removeBook = async (book: Book) => {
    if (!window.confirm(`确定删除《${book.title}》？该操作不可恢复。`)) return
    try {
      await api.deleteBook(book.id)
      toast.success('已删除')
      loadBooks()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-slate-100">书架</h2>
        <p className="mt-1 text-sm text-slate-500">
          导入一本小说 → 拆书 → 续写。所有数据保存在你本机。
        </p>
      </header>

      <div className="flex gap-2">
        {(
          [
            ['upload', '上传 TXT / EPUB'],
            ['paste', '手动粘贴'],
            ['fanqie', '番茄小说'],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`btn ${
              tab === key ? 'bg-indigo-600 text-white' : 'border border-slate-700 text-slate-300 hover:bg-slate-800'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'upload' ? <UploadPanel onDone={loadBooks} navigate={navigate} /> : null}
      {tab === 'paste' ? <PastePanel onDone={loadBooks} navigate={navigate} /> : null}
      {tab === 'fanqie' ? <FanqiePanel onJob={setJobId} onDone={loadBooks} navigate={navigate} /> : null}

      {jobId ? <JobProgress jobId={jobId} onDone={loadBooks} onSettled={() => loadBooks()} /> : null}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-300">我的书（{books.length}）</h3>
          <button className="btn-ghost !px-2 !py-1 text-xs" onClick={loadBooks}>
            刷新
          </button>
        </div>

        {loading ? (
          <div className="card text-sm text-slate-500">加载中…</div>
        ) : books.length === 0 ? (
          <div className="card text-sm text-slate-500">还没有书，先用上面的方式导入一本吧。</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {books.map((book) => (
              <div key={book.id} className="card group relative flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <button
                    className="text-left text-base font-medium text-slate-100 hover:text-indigo-300"
                    onClick={() => navigate(`/books/${book.id}`)}
                  >
                    《{book.title}》
                  </button>
                  <button
                    className="opacity-0 transition group-hover:opacity-100 text-xs text-rose-400 hover:text-rose-300"
                    onClick={() => removeBook(book)}
                  >
                    删除
                  </button>
                </div>
                <p className="text-xs text-slate-500">
                  {book.author || '佚名'} · {formatNumber(book.total_chapters)} 章 ·{' '}
                  {formatDate(book.updated_at)}
                </p>
                <div className="flex flex-wrap gap-1">
                  <span className="tag bg-slate-800 text-slate-400">{sourceLabel(book.source)}</span>
                  <span
                    className={`tag ${
                      book.analyzed ? 'bg-emerald-950 text-emerald-300' : 'bg-amber-950 text-amber-300'
                    }`}
                  >
                    {book.analyzed ? '已拆书' : '未拆书'}
                  </span>
                </div>
                {book.intro ? (
                  <p className="line-clamp-2 text-xs text-slate-500">{book.intro.slice(0, 120)}</p>
                ) : null}
                <div className="mt-1 flex gap-2">
                  <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => navigate(`/books/${book.id}`)}>
                    打开
                  </button>
                  <button
                    className="btn-primary !px-2 !py-1 text-xs"
                    onClick={() => navigate(`/books/${book.id}/write`)}
                  >
                    续写
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

// ======================================================================
function UploadPanel({ onDone, navigate }: { onDone: () => void; navigate: (p: string) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!file) {
      toast.error('请先选择 TXT 或 EPUB 文件')
      return
    }
    setBusy(true)
    try {
      const res = await api.uploadBook(file, title, author)
      toast.success(res.message)
      onDone()
      navigate(`/books/${res.book.id}`)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <label className="label">书名（留空则自动识别）</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：xx修仙传" />
        </div>
        <div>
          <label className="label">作者（可选）</label>
          <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label">选择文件（.txt / .epub）</label>
        <input
          type="file"
          accept=".txt,.epub"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full cursor-pointer rounded-lg border border-dashed border-slate-700 bg-slate-950/40 px-3 py-6 text-sm text-slate-400 file:mr-3 file:rounded-md file:border-0 file:bg-indigo-600 file:px-3 file:py-1.5 file:text-white"
        />
        <p className="mt-2 text-xs text-slate-500">
          自动识别 UTF-8 / GBK / GB18030 / BIG5 编码，并按「第X章」「第X回」「Chapter N」切分章节。
        </p>
      </div>
      <button className="btn-primary" onClick={submit} disabled={busy}>
        {busy ? '正在上传解析…' : '导入并创建书籍'}
      </button>
    </div>
  )
}

// ======================================================================
function PastePanel({ onDone, navigate }: { onDone: () => void; navigate: (p: string) => void }) {
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [content, setContent] = useState('')
  const [split, setSplit] = useState(true)
  const [chapterTitle, setChapterTitle] = useState('第1章')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!content.trim()) {
      toast.error('请粘贴正文内容')
      return
    }
    setBusy(true)
    try {
      const res = await api.pasteBook({
        title: title || '粘贴的小说',
        author,
        content,
        chapter_title: chapterTitle,
        split,
      })
      toast.success(res.message)
      onDone()
      navigate(`/books/${res.book.id}`)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <div>
          <label className="label">书名</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="label">作者（可选）</label>
          <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} />
        </div>
        <div>
          <label className="label">不自动切分时的章节名</label>
          <input className="input" value={chapterTitle} onChange={(e) => setChapterTitle(e.target.value)} />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-400">
        <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} />
        按「第X章」自动切分章节
      </label>
      <textarea
        className="input h-64 resize-y font-mono"
        placeholder="把小说正文粘贴到这里…"
        value={content}
        onChange={(e) => setContent(e.target.value)}
      />
      <button className="btn-primary" onClick={submit} disabled={busy}>
        {busy ? '导入中…' : '导入并创建书籍'}
      </button>
    </div>
  )
}

// ======================================================================
function FanqiePanel({
  onJob,
  onDone,
  navigate,
}: {
  onJob: (id: string) => void
  onDone: () => void
  navigate: (p: string) => void
}) {
  const [status, setStatus] = useState<FanqieStatus | null>(null)
  const [keyword, setKeyword] = useState('')
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState<{ bookId: string; title: string } | null>(null)
  const [maxChapters, setMaxChapters] = useState<number | ''>('')
  const [analyze, setAnalyze] = useState(true)
  const [startChapter, setStartChapter] = useState(1)

  useEffect(() => {
    api
      .fanqieStatus()
      .then(setStatus)
      .catch((error) => toast.error((error as Error).message))
  }, [])

  const doSearch = async () => {
    if (!keyword.trim()) return
    setBusy(true)
    setResult(null)
    try {
      const res = await api.fanqieSearch(keyword.trim())
      setResult(res.result)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const doImport = async () => {
    if (!importing) return
    try {
      const res = await api.fanqieImport({
        book_id: importing.bookId,
        title: importing.title,
        max_chapters: maxChapters === '' ? null : Number(maxChapters),
        start_chapter: startChapter,
        analyze,
      })
      toast.success(res.message)
      onJob(res.job_id)
      setImporting(null)
      onDone()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const items: any[] = Array.isArray(result)
    ? result
    : result && typeof result === 'object' && Array.isArray(result.results)
      ? result.results
      : result && typeof result === 'object' && Array.isArray(result.data)
        ? result.data
        : result && typeof result === 'object' && Array.isArray(result.books)
          ? result.books
          : []

  if (!status) {
    return <div className="card text-sm text-slate-500">正在检测番茄 MCP 状态…</div>
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${status.available ? 'bg-emerald-500' : 'bg-amber-500'}`} />
        <span className={status.available ? 'text-emerald-300' : 'text-amber-300'}>
          {status.available ? '番茄 MCP 已连接' : '番茄 MCP 不可用'}
        </span>
        <span className="text-xs text-slate-500">{status.message}</span>
      </div>

      {!status.configured ? (
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-xs text-slate-400">
          未配置番茄 MCP。到「设置 → 番茄 MCP」填写启动命令：
          <code className="mx-1 rounded bg-slate-800 px-1">npx -y @fysh925/mcp-server-fanqie</code>
          保存后回这里刷新即可。
          <br />
          不想配也没关系，用上面的「上传 TXT / EPUB」或「手动粘贴」一样能完成拆书和续写。
        </p>
      ) : !status.available ? (
        <p className="whitespace-pre-wrap rounded-lg border border-amber-900/60 bg-amber-950/30 p-3 text-xs text-amber-300">
          ⚠️ 番茄 MCP 当前不可用：{status.message}
          <br />
          <br />
          可以到「设置 → 番茄 MCP」点「运行完整自检」，它会告诉你卡在哪一步。
          如果只是上游接口挂了（常见情况），这是第三方服务的问题，本项目无法修复，
          请先改用「上传 TXT / EPUB」导入。
        </p>
      ) : null}

      <div className="flex gap-2">
        <input
          className="input"
          placeholder="输入书名关键词"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch()}
          disabled={!status.available}
        />
        <button className="btn-primary shrink-0" onClick={doSearch} disabled={!status.available || busy}>
          {busy ? '搜索中…' : '搜索'}
        </button>
      </div>

      {items.length > 0 ? (
        <div className="max-h-72 space-y-2 overflow-y-auto">
          {items.map((item, index) => {
            const id = pickBookId(item)
            const name = pickTitle(item) || `结果 ${index + 1}`
            return (
              <div
                key={`${id}-${index}`}
                className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{name}</p>
                  <p className="text-[11px] text-slate-500">ID: {id || '未知'}</p>
                </div>
                <button
                  className="btn-ghost shrink-0 !px-2 !py-1 text-xs"
                  disabled={!id}
                  onClick={() => {
                    setImporting({ bookId: id, title: name })
                    setMaxChapters('')
                    setStartChapter(1)
                  }}
                >
                  导入
                </button>
              </div>
            )
          })}
        </div>
      ) : result ? (
        <pre className="max-h-72 overflow-auto rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-400">
          {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
        </pre>
      ) : null}

      <Modal
        open={importing !== null}
        title={`导入《${importing?.title ?? ''}》`}
        onClose={() => setImporting(null)}
        footer={
          <>
            <button className="btn-ghost" onClick={() => setImporting(null)}>
              取消
            </button>
            <button className="btn-primary" onClick={doImport}>
              开始下载
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="label">番茄书籍 ID</label>
            <input
              className="input"
              value={importing?.bookId ?? ''}
              onChange={(e) =>
                setImporting((prev) => (prev ? { ...prev, bookId: e.target.value } : prev))
              }
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">起始章节</label>
              <input
                type="number"
                min={1}
                className="input"
                value={startChapter}
                onChange={(e) => setStartChapter(Number(e.target.value) || 1)}
              />
            </div>
            <div>
              <label className="label">最多下载（留空=全部）</label>
              <input
                type="number"
                min={1}
                className="input"
                value={maxChapters}
                onChange={(e) => setMaxChapters(e.target.value === '' ? '' : Number(e.target.value))}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-400">
            <input type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} />
            下载完成后立即拆书
          </label>
        </div>
      </Modal>
    </div>
  )
}
