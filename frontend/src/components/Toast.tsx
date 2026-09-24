/** 极简 toast：模块级单例，任何地方都能调用 toast.error('...')，由 <Toaster/> 渲染。 */

import { useSyncExternalStore } from 'react'

export type ToastKind = 'info' | 'success' | 'error'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

let items: ToastItem[] = []
let seq = 0
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): ToastItem[] {
  return items
}

export function dismissToast(id: number) {
  items = items.filter((item) => item.id !== id)
  emit()
}

function push(kind: ToastKind, message: string, timeout = 4000) {
  const id = ++seq
  items = [...items, { id, kind, message }]
  emit()
  if (timeout > 0) {
    window.setTimeout(() => dismissToast(id), timeout)
  }
}

export const toast = {
  info: (message: string) => push('info', message),
  success: (message: string) => push('success', message),
  error: (message: string) => push('error', message, 8000),
}

const STYLES: Record<ToastKind, string> = {
  info: 'border-slate-700 bg-slate-800 text-slate-100',
  success: 'border-emerald-800 bg-emerald-950 text-emerald-200',
  error: 'border-rose-800 bg-rose-950 text-rose-200',
}

export function Toaster() {
  const list = useSyncExternalStore(subscribe, getSnapshot)
  if (list.length === 0) return null
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-[min(420px,92vw)] flex-col gap-2">
      {list.map((item) => (
        <div
          key={item.id}
          onClick={() => dismissToast(item.id)}
          className={`pointer-events-auto cursor-pointer whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm shadow-lg ${STYLES[item.kind]}`}
        >
          {item.message}
        </div>
      ))}
    </div>
  )
}

export default Toaster
