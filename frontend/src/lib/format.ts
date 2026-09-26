/** 格式化工具：数字、费用、时间、字数。 */

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '0'
  return Math.round(value).toLocaleString('zh-CN')
}

export function formatCost(usd: number | null | undefined, cny?: number | null): string {
  const u = usd ?? 0
  const text = `$${u.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`
  if (cny === undefined || cny === null) return text
  return `${text}（约 ¥${cny.toFixed(4)}）`
}

export function formatWords(count: number | null | undefined): string {
  const n = count ?? 0
  if (n >= 10000) return `${(n / 10000).toFixed(2)} 万字`
  return `${formatNumber(n)} 字`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)

  // 后端存的是 UTC，但 SQLite 读回来可能没有时区标记，这里做一次兜底
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

const SOURCE_LABELS: Record<string, string> = {
  fanqie: '番茄',
  txt: 'TXT',
  epub: 'EPUB',
  manual: '手动',
  original: '原创',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

export const MODE_LABELS: Record<string, string> = {
  continue: '只生成正文',
  outline: '只生成大纲',
  polish: '润色正文',
  rewrite: '重写正文',
}

export const SECTION_LABELS: Record<string, string> = {
  all: '全部',
  outline: '总纲',
  characters: '人物卡',
  worldview: '世界观',
  timeline: '时间线',
  foreshadows: '伏笔',
  style: '文风',
}
