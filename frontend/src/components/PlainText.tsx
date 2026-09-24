/** 正文渲染：按行分段，识别「第X章」这类标题行，避免引入 markdown 依赖。 */
import { useMemo } from 'react'

interface Props {
  text: string
  className?: string
}

const HEADING = /^\s*(第[0-9一二三四五六七八九十百千万两]+[章回节卷][^\n]{0,40}|#{1,3}\s+.+)\s*$/

export default function PlainText({ text, className }: Props) {
  const lines = useMemo(() => (text || '').replace(/\r\n/g, '\n').split('\n'), [text])

  return (
    <div className={`space-y-3 whitespace-pre-wrap break-words text-[15px] leading-8 text-slate-200 ${className ?? ''}`}>
      {lines.map((line, index) => {
        const trimmed = line.trim()
        if (!trimmed) return null
        if (HEADING.test(trimmed)) {
          const title = trimmed.replace(/^#{1,3}\s+/, '')
          return (
            <h3 key={index} className="pt-2 text-base font-semibold text-indigo-300">
              {title}
            </h3>
          )
        }
        return <p key={index}>{trimmed}</p>
      })}
    </div>
  )
}
