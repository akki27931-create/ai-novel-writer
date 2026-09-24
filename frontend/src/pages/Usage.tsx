/** 用量页：token 消耗与预估费用统计。 */
import { useEffect, useState } from 'react'
import { api } from '../api'
import { toast } from '../components/Toast'
import { formatCost, formatDate, formatNumber } from '../lib/format'
import type { UsageRow, UsageSummary } from '../types'

export default function Usage() {
  const [data, setData] = useState<UsageSummary | null>(null)
  const [bookTitles, setBookTitles] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const [usage, books] = await Promise.all([api.getUsage(20), api.listBooks()])
      setData(usage)
      const map: Record<string, string> = {}
      books.forEach((book) => {
        map[String(book.id)] = book.title
      })
      setBookTitles(map)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  if (loading) return <div className="card text-sm text-slate-500">加载中…</div>
  if (!data) return <div className="card text-sm text-slate-500">暂无数据。</div>

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">用量与费用</h2>
          <p className="mt-1 text-sm text-slate-500">
            费用为按设置页单价估算，实际以 DeepSeek 官方账单为准。
          </p>
        </div>
        <button className="btn-ghost" onClick={load}>
          刷新
        </button>
      </header>

      <div className="grid gap-3 md:grid-cols-4">
        <StatCard label="请求数" value={formatNumber(data.total_requests)} />
        <StatCard label="输入 tokens" value={formatNumber(data.total_input_tokens)} />
        <StatCard label="输出 tokens" value={formatNumber(data.total_output_tokens)} />
        <StatCard
          label="预估费用"
          value={`$${data.total_cost_usd.toFixed(4)}`}
          hint={`约 ¥${data.total_cost_cny.toFixed(4)}`}
        />
      </div>

      <GroupTable title="按模型" rows={data.by_model} kind="model" />
      <GroupTable title="按任务类型" rows={data.by_task} kind="task" />
      <GroupTable title="按书籍" rows={data.by_book} kind="book" books={bookTitles} />

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-300">最近 20 条请求</h3>
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full text-xs">
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                <th className="px-3 py-2 text-left">时间</th>
                <th className="px-3 py-2 text-left">书籍</th>
                <th className="px-3 py-2 text-left">任务</th>
                <th className="px-3 py-2 text-left">模型</th>
                <th className="px-3 py-2 text-right">输入</th>
                <th className="px-3 py-2 text-right">输出</th>
                <th className="px-3 py-2 text-right">费用</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {data.recent.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-center text-slate-500">
                    暂无记录
                  </td>
                </tr>
              ) : (
                data.recent.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-900/60">
                    <td className="px-3 py-2 text-slate-400">{formatDate(row.created_at)}</td>
                    <td className="px-3 py-2 text-slate-400">{row.book_id ?? '-'}</td>
                    <td className="px-3 py-2 text-slate-300">{taskLabel(row.task_type)}</td>
                    <td className="px-3 py-2 text-slate-400">{row.model}</td>
                    <td className="px-3 py-2 text-right text-slate-400">{formatNumber(row.input_tokens)}</td>
                    <td className="px-3 py-2 text-right text-slate-400">{formatNumber(row.output_tokens)}</td>
                    <td className="px-3 py-2 text-right text-slate-300">
                      {formatCost(row.cost_usd, row.cost_cny)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

const TASK_LABELS: Record<string, string> = {
  continue: '续写正文',
  outline: '生成大纲',
  polish: '润色',
  rewrite: '重写',
  summarize: '章节摘要',
  analyze: '拆书',
  consistency: '伏笔/矛盾检查',
}

function taskLabel(task: string): string {
  return TASK_LABELS[task] ?? task ?? '-'
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-slate-100">{value}</p>
      {hint ? <p className="text-[11px] text-slate-500">{hint}</p> : null}
    </div>
  )
}

function GroupTable({
  title,
  rows,
  kind,
  books,
}: {
  title: string
  rows: UsageRow[]
  kind: 'model' | 'task' | 'book'
  books?: Record<string, string>
}) {
  const label = (key: any): string => {
    if (kind === 'task') return taskLabel(String(key))
    if (kind === 'book') return books?.[String(key)] ? `《${books[String(key)]}》` : `书籍 #${key}`
    return String(key ?? '-')
  }

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-300">{title}</h3>
      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full text-xs">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">名称</th>
              <th className="px-3 py-2 text-right">请求数</th>
              <th className="px-3 py-2 text-right">输入 tokens</th>
              <th className="px-3 py-2 text-right">输出 tokens</th>
              <th className="px-3 py-2 text-right">费用（USD / CNY）</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-slate-500">
                  暂无数据
                </td>
              </tr>
            ) : (
              rows.map((row, index) => (
                <tr key={`${row.key}-${index}`} className="hover:bg-slate-900/60">
                  <td className="px-3 py-2 text-slate-300">{label(row.key)}</td>
                  <td className="px-3 py-2 text-right text-slate-400">{formatNumber(row.requests)}</td>
                  <td className="px-3 py-2 text-right text-slate-400">{formatNumber(row.input_tokens)}</td>
                  <td className="px-3 py-2 text-right text-slate-400">{formatNumber(row.output_tokens)}</td>
                  <td className="px-3 py-2 text-right text-slate-300">{formatCost(row.cost_usd, row.cost_cny)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
