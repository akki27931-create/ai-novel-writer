/** 后台任务进度条：定时轮询 /api/jobs/{id}，结束后回调。 */
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Job } from '../types'

interface Props {
  jobId: string | null
  onDone?: (job: Job) => void
  onSettled?: (job: Job) => void
}

/** 把秒数写成「3 分 12 秒」。 */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s ? `${m} 分 ${s} 秒` : `${m} 分`
}

export default function JobProgress({ jobId, onDone, onSettled }: Props) {
  const [job, setJob] = useState<Job | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const settledRef = useRef<string | null>(null)
  const doneRef = useRef(onDone)
  const settledCbRef = useRef(onSettled)
  doneRef.current = onDone
  settledCbRef.current = onSettled

  const running = job?.status === 'running' || job?.status === 'pending'

  // 记时器：后端在一次模型调用期间不会有新的进度上报，
  // 没有这个"已运行 N 秒"用户会以为任务死了。
  useEffect(() => {
    if (!running) return
    const startedAt = Date.now()
    setElapsed(0)
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - startedAt) / 1000)),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [running, jobId])

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return
    }
    let cancelled = false
    settledRef.current = null
    setJob(null)

    const tick = async () => {
      try {
        const current = await api.job(jobId)
        if (cancelled) return
        setJob(current)
        if (current.status === 'running' || current.status === 'pending') {
          window.setTimeout(tick, 1200)
          return
        }
        if (settledRef.current !== jobId) {
          settledRef.current = jobId
          settledCbRef.current?.(current)
          if (current.status === 'success') doneRef.current?.(current)
        }
      } catch (error) {
        if (!cancelled) {
          setJob({
            id: jobId,
            kind: '',
            status: 'error',
            progress: 0,
            message: (error as Error).message,
            total: 0,
            done: 0,
            result: null,
            error: (error as Error).message,
            created_at: new Date().toISOString(),
          })
        }
      }
    }

    tick()
    return () => {
      cancelled = true
    }
  }, [jobId])

  if (!job) return null

  const percent = Math.round((job.progress || 0) * 100)
  const color =
    job.status === 'error'
      ? 'bg-rose-500'
      : job.status === 'success'
        ? 'bg-emerald-500'
        : 'bg-indigo-500'

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/80 p-3">
      <div className="mb-2 flex items-center justify-between text-xs text-slate-400">
        <span>
          {job.kind || '任务'} ·{' '}
          {job.status === 'running'
            ? '进行中'
            : job.status === 'success'
              ? '已完成'
              : job.status === 'error'
                ? '失败'
                : job.status === 'cancelled'
                  ? '已取消'
                  : '等待中'}
        </span>
        <span>
          {job.total > 0 ? `${job.done}/${job.total} · ` : ''}
          {percent}%
          {running ? ` · 已运行 ${formatDuration(elapsed)}` : ''}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full ${color} transition-all`} style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-2 whitespace-pre-wrap text-xs text-slate-400">
        {job.error || job.message || '处理中…'}
      </p>
      {running && elapsed >= 15 ? (
        <p className="mt-1 text-[11px] text-slate-500">
          正在等待模型返回。推理模型一次生成较多内容时需要 1~3 分钟，等待期间进度不动是正常的 ——
          超过设置里的 llm_timeout 会被主动中断并给出提示，不会一直挂着。
        </p>
      ) : null}
      {job.status === 'running' ? (
        <button
          className="btn-ghost mt-2 !px-2 !py-1 text-xs"
          onClick={() => api.cancelJob(job.id).catch(() => undefined)}
        >
          取消任务
        </button>
      ) : null}
    </div>
  )
}
