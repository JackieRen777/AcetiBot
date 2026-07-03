import { Children, cloneElement, isValidElement } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import SensorRadar from './SensorRadar'

function formatSourcePreview(text = '') {
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .flatMap(line => line.split(/(?<=[。！？；])/))
    .map(line => line.trim())
    .filter(Boolean)
}

function CitationTooltip({ source }) {
  if (!source) return null

  const previewLines = formatSourcePreview(source.text).slice(0, 6)

  return (
    <div className="pointer-events-none absolute left-1/2 top-0 z-20 w-[22rem] max-w-[min(22rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-[calc(100%+12px)] rounded-2xl border border-[#dfd8ce] bg-[rgba(255,252,248,0.98)] p-3 text-left shadow-[0_18px_50px_rgba(41,37,36,0.14)] backdrop-blur-sm">
      <div className="absolute left-1/2 top-full h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-[#dfd8ce] bg-[rgba(255,252,248,0.98)]" />
      <div className="flex items-start gap-2">
        <span className="mt-0.5 rounded-full bg-[#2f241f] px-2 py-0.5 text-[10px] font-semibold tracking-[0.08em] text-white">
          [{source.id}]
        </span>
        <div className="min-w-0">
          <p className="text-[12px] font-medium leading-5 text-ink">
            {source.title}
          </p>
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-muted">
            <span className="rounded bg-[#efe7dc] px-1.5 py-0.5 text-[#6b5d52]">{source.type}</span>
            {source.page && <span>第 {source.page} 页</span>}
            {source.journal && <span>{source.journal}</span>}
            {source.assignee && <span>{source.assignee}</span>}
            {source.year && <span>{source.year}</span>}
          </div>
        </div>
      </div>
      {previewLines.length > 0 && (
        <div className="mt-3 space-y-1.5 border-t border-[#ece5dc] pt-2.5 text-[11px] leading-5 text-[#4e4e4e]">
          {previewLines.map((line, index) => (
            <p key={`${source.id}-${index}`} className="m-0">
              {line}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function CitationMarker({ source }) {
  return (
    <span className="group relative mx-[1px] inline-flex align-super leading-none">
      <button
        type="button"
        className="rounded-sm px-0.5 text-[11px] font-semibold text-[#6b5d52] decoration-[#b9ab9d] underline underline-offset-[2px] transition-colors hover:text-[#2f241f] focus:outline-none focus:text-[#2f241f]"
      >
        [{source.id}]
      </button>
      <div className="invisible opacity-0 transition duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        <CitationTooltip source={source} />
      </div>
    </span>
  )
}

function injectCitations(content, sources, handlers) {
  const parts = content.split(/(\[\d+\])/g)
  return (
    <>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/)
        if (!match) return part
        const id = parseInt(match[1])
        const source = sources.find(item => item.id === id)
        if (!source) return part
        return <CitationMarker key={i} source={source} {...handlers} />
      })}
    </>
  )
}

function renderCitationChildren(node, sources, handlers) {
  if (typeof node === 'string') {
    return injectCitations(node, sources, handlers)
  }

  if (Array.isArray(node)) {
    return Children.map(node, child => renderCitationChildren(child, sources, handlers))
  }

  if (isValidElement(node) && node.props?.children) {
    return cloneElement(node, {
      ...node.props,
      children: renderCitationChildren(node.props.children, sources, handlers),
    })
  }

  return node
}

export default function ChatMessage({ message }) {
  const { role, content, sources = [], fileName, sensorData, streaming } = message

  const isUser = role === 'user'

  if (isUser) {
    return (
      <div className="flex flex-col items-end mb-4 gap-2">
        <div className="max-w-[75%] bg-ink text-white rounded-2xl rounded-br-sm px-4 py-2.5 text-sm space-y-1">
          {content && <p>{content}</p>}
          {fileName && (
            <p className="text-xs opacity-60">📎 {fileName}</p>
          )}
        </div>
        {sensorData && <SensorRadar data={sensorData} />}
      </div>
    )
  }

  return (
    <div className="mb-6">
      <div className="flex items-start gap-3">
        {/* Avatar */}
        <div className="shrink-0 w-7 h-7 rounded-full bg-ink flex items-center justify-center">
          <span className="text-white text-xs font-display">醅</span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="prose prose-sm max-w-none text-ink leading-relaxed
            [&_table]:border-collapse [&_table]:w-full [&_table]:text-sm
            [&_th]:bg-[#f0efed] [&_th]:text-left [&_th]:px-3 [&_th]:py-2 [&_th]:border [&_th]:border-hairline
            [&_td]:px-3 [&_td]:py-2 [&_td]:border [&_td]:border-hairline
            [&_strong]:font-medium [&_p]:mb-2 [&_p:last-child]:mb-0">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[]}
              components={{
                del: ({ children }) => <span>{children}</span>, // 禁用删除线渲染
                p: ({ children }) => <p>{renderCitationChildren(children, sources)}</p>,
                li: ({ children }) => <li>{renderCitationChildren(children, sources)}</li>,
                td: ({ children }) => <td>{renderCitationChildren(children, sources)}</td>,
                th: ({ children }) => <th>{renderCitationChildren(children, sources)}</th>,
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
          {streaming && !content && (
            <div className="flex gap-1 mt-2">
              {[0, 1, 2].map(i => (
                <span
                  key={i}
                  className="w-1.5 h-1.5 rounded-full bg-[#a8a29e] animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }}
                />
              ))}
            </div>
          )}
          {/* Source list */}
          {sources.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {sources.map(s => (
                <button
                  key={s.id}
                  type="button"
                  title={`${s.title}${s.page ? `｜第 ${s.page} 页` : ''}`}
                  className="text-xs bg-[#f0efed] hover:bg-[#e7e5e4] border border-hairline rounded px-2 py-0.5 text-muted transition-colors">
                  [{s.id}] {s.title.slice(0, 28)}{s.title.length > 28 ? '…' : ''}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
