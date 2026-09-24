/** 后台任务进度条：定时轮询 /api/jobs/{id}，结束后回调。 */
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Job } from '../types'

interface Props {
  jobId: string | null
  onDone?: (job: Job) => void
  onSettled?: (job: Job) => void
}

export default function JobProgress({ jobId, onDone, onSettled }: Props) {
  const [job, setJob] = useState<Job | null>(null)
  const settledRef = useRef<string | null>(null)
  const doneRef = useRef(onDone)
  const settledCbRef = useRef(onSettled)
  doneRef.current = onDone
  settledCbRef.current = onSettled

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
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full ${color} transition-all`} style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-2 whitespace-pre-wrap text-xs text-slate-400">
        {job.error || job.message || '处理中…'}
      </p>
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
