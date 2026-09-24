/** 全局布局：左侧导航栏 + 右侧内容区，底部显示后端连接状态。 */
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'

const NAV = [
  { to: '/', label: '书架', icon: '📚', end: true },
  { to: '/settings', label: '设置', icon: '⚙️', end: false },
  { to: '/usage', label: '用量', icon: '📊', end: false },
]

export default function Layout() {
  const [online, setOnline] = useState<boolean | null>(null)
  const [version, setVersion] = useState('')

  useEffect(() => {
    let alive = true
    const ping = async () => {
      try {
        const res = await api.health()
        if (alive) {
          setOnline(true)
          setVersion(res.version)
        }
      } catch {
        if (alive) setOnline(false)
      }
    }
    ping()
    const timer = window.setInterval(ping, 30000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  return (
    <div className="flex h-full">
      <aside className="flex w-[220px] shrink-0 flex-col border-r border-slate-800 bg-slate-900/60">
        <div className="border-b border-slate-800 px-4 py-5">
          <h1 className="text-base font-semibold text-slate-100">AI 小说续写</h1>
          <p className="mt-1 text-[11px] text-slate-500">本地拆书 · RAG 续写</p>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                  isActive
                    ? 'bg-indigo-600/20 text-indigo-200'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`
              }
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-800 px-4 py-3 text-[11px]">
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                online === null ? 'bg-slate-500' : online ? 'bg-emerald-500' : 'bg-rose-500'
              }`}
            />
            <span className="text-slate-400">
              {online === null ? '检测中…' : online ? `后端已连接 v${version}` : '后端未连接'}
            </span>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-6 py-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
