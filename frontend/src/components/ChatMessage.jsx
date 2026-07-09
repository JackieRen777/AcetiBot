import { Children, cloneElement, isValidElement, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import SensorRadar from './SensorRadar'

const LOADING_MESSAGES = [
  '思考中',
  '整理资料中',
  '检索知识库中',
  '分析证据中',
  '梳理逻辑中',
  '组织语言中',
  '核对引用来源中',
  '深度分析中',
  '构建回答框架中',
]

function LoadingIndicator() {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => {
      setIndex(prev => (prev + 1) % LOADING_MESSAGES.length)
    }, 2500)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="px-1 py-2 flex items-center gap-2">
      <span className="text-sm leading-6 text-[#4e4e4e]">{LOADING_MESSAGES[index]}</span>
      <span className="flex gap-1">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#a8a29e]"
            style={{ animationDelay: `${i * 0.18}s` }}
          />
        ))}
      </span>
    </div>
  )
}

const API_BASE_URL = import.meta.env.VITE_API_URL
  || (import.meta.env.VITE_API_HOST ? `https://${import.meta.env.VITE_API_HOST}` : 'http://localhost:8013')

const CITATION_TOKEN_REGEX = /(@@CITE:(\d+)@@|\[(\d+)\])/g

function buildSourceUrl(source) {
  if (!source?.relative_path) return null
  const baseUrl = `${API_BASE_URL}/document?path=${encodeURIComponent(source.relative_path)}`
  return source.page ? `${baseUrl}#page=${source.page}` : baseUrl
}

function toStructuredMarkdown(content = '') {
  return content
    .split('\n')
    .map(line => {
      const trimmed = line.trim()
      if (/^[一二三四五六七八九十]+、/.test(trimmed)) return `# ${trimmed}`
      if (/^（[一二三四五六七八九十]+）/.test(trimmed)) return `## ${trimmed}`
      if (/^\d+、/.test(trimmed)) return `### ${trimmed}`
      return line
    })
    .join('\n')
}

function CitationTooltip({ source, displayId }) {
  if (!source) return null

  const targetPage = source.citedPageLabels?.join('、')
    || source.page_label
    || source.citedPages?.join('、')
    || source.page

  return (
    <div className="pointer-events-none absolute left-1/2 top-0 z-20 w-[22rem] max-w-[min(22rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-[calc(100%+12px)] rounded-2xl border border-[#dfd8ce] bg-[rgba(255,252,248,0.98)] p-3 text-left shadow-[0_18px_50px_rgba(41,37,36,0.14)] backdrop-blur-sm">
      <div className="absolute left-1/2 top-full h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 border-b border-r border-[#dfd8ce] bg-[rgba(255,252,248,0.98)]" />
      <div className="flex items-start gap-2">
        <span className="mt-0.5 rounded-full bg-[#2f241f] px-2 py-0.5 text-[10px] font-semibold tracking-[0.08em] text-white">
          [{displayId}]
        </span>
        <div className="min-w-0">
          <p className="text-[12px] font-medium leading-5 text-ink">{source.title}</p>
          <div className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-muted">
            <span className="rounded bg-[#efe7dc] px-1.5 py-0.5 text-[#6b5d52]">{source.type}</span>
            {targetPage && <span>定位页：{targetPage}</span>}
          </div>
          <div className="mt-2 space-y-1 text-[11px] leading-5 text-[#5f564d]">
            <p className="m-0">作者/机构：{source.display_author || '未标注'}</p>
            <p className="m-0">时间：{source.display_time || '时间未标注'}</p>
            {source.journal && <p className="m-0">期刊：{source.journal}</p>}
            {source.patent_no && <p className="m-0">专利号：{source.patent_no}</p>}
            {source.standard_no && <p className="m-0">标准号：{source.standard_no}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}

function CitationMarker({ source, displayId }) {
  const href = buildSourceUrl(source)

  return (
    <span className="group relative mx-[1px] inline-flex align-super leading-none">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-sm px-0.5 text-[11px] font-semibold text-[#6b5d52] decoration-[#b9ab9d] underline underline-offset-[2px] transition-colors hover:text-[#2f241f] focus:outline-none focus:text-[#2f241f]"
        >
          [{displayId}]
        </a>
      ) : (
        <span className="rounded-sm px-0.5 text-[11px] font-semibold text-[#6b5d52] underline decoration-[#b9ab9d] underline-offset-[2px]">
          [{displayId}]
        </span>
      )}
      <div className="invisible opacity-0 transition duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        <CitationTooltip source={source} displayId={displayId} />
      </div>
    </span>
  )
}

function buildCitationSourceMap(sources = []) {
  const map = new Map()
  sources.forEach(source => {
    const rawIds = source.rawCitationIds?.length ? source.rawCitationIds : [source.rawCitationId]
    rawIds.forEach(rawId => {
      const numericId = Number(rawId)
      if (!Number.isNaN(numericId)) {
        map.set(numericId, source)
      }
    })
  })
  return map
}

function injectCitations(content, sourceMap) {
  const parts = []
  let lastIndex = 0
  let match
  let lastDisplayId = null  // 用于去除相邻重复引用

  while ((match = CITATION_TOKEN_REGEX.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }

    const rawId = Number(match[2] || match[3])
    const source = sourceMap.get(rawId)
    if (!source) {
      parts.push(match[0])
      lastDisplayId = null
    } else {
      const displayId = source.displayId || source.id || rawId
      // 跳过与上一个相邻的相同引用（去除 [1][1] 重复）
      if (displayId !== lastDisplayId) {
        parts.push(
          <CitationMarker
            key={`${match.index}-${rawId}`}
            source={source}
            displayId={displayId}
          />
        )
        lastDisplayId = displayId
      }
    }

    lastIndex = CITATION_TOKEN_REGEX.lastIndex
  }

  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }

  CITATION_TOKEN_REGEX.lastIndex = 0
  return <>{parts}</>
}

function renderCitationChildren(node, sourceMap) {
  if (typeof node === 'string') {
    return injectCitations(node, sourceMap)
  }

  if (Array.isArray(node)) {
    return Children.map(node, child => renderCitationChildren(child, sourceMap))
  }

  if (isValidElement(node) && node.props?.children) {
    return cloneElement(node, {
      ...node.props,
      children: renderCitationChildren(node.props.children, sourceMap),
    })
  }

  return node
}

// 过滤占位符文字，无效值返回 null
const PLACEHOLDER_PATTERNS = /^(未标注|作者未标注|发布机构未标注|时间未标注|无|—|-)$/

function filterMeta(value) {
  if (!value) return null
  const s = String(value).trim()
  if (!s || PLACEHOLDER_PATTERNS.test(s)) return null
  return s
}

// 清洗标题前缀（学位论文常见冗余前缀）
const TITLE_PREFIX_RE = /^(硕士学位论文题目[：:]\s*|博士学位论文题目[：:]\s*|申请硕士学位论文[：:]\s*|学位论文[：:]\s*|题目[：:]\s*)/

function cleanTitle(title) {
  if (!title) return title
  return title.replace(TITLE_PREFIX_RE, '').trim()
}

function SourceCard({ source }) {
  const href = buildSourceUrl(source)
  const title = cleanTitle(source.title || '')

  // 过滤占位符，只保留有效字段
  const authorVal = filterMeta(source.display_author)
  const timeVal = filterMeta(source.display_time || source.year)
  const journalVal = filterMeta(source.journal)
  const patentVal = filterMeta(source.patent_no)
  const standardVal = filterMeta(source.standard_no)

  // 避免期刊名与标题相同时重复显示
  const effectiveJournal = journalVal && journalVal !== title ? journalVal : null

  const metaParts = [
    authorVal,
    timeVal,
    effectiveJournal,
    patentVal ? `专利号 ${patentVal}` : null,
    standardVal,
  ].filter(Boolean)
  const metaLine = metaParts.join(' · ')

  return (
    <a
      href={href || undefined}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded-2xl border border-[#e7e5e4] bg-[#faf8f5] px-4 py-3 no-underline transition-colors hover:border-[#cfc6ba] hover:bg-[#f5f0ea]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="m-0 text-sm font-medium leading-6 text-ink">
            [{source.displayId || source.id}] {title}
          </p>
          {metaLine && (
            <p className="m-0 mt-0.5 text-[11px] leading-5 text-[#9b8f84]">{metaLine}</p>
          )}
        </div>
        <span className="shrink-0 rounded-full bg-[#efe7dc] px-2 py-0.5 text-[11px] text-[#6b5d52]">
          {source.type}
        </span>
      </div>
    </a>
  )
}

export default function ChatMessage({ message }) {
  const { role, content, sources = [], citationCatalog = [], fileName, sensorData, streaming } = message
  const [showAllSources, setShowAllSources] = useState(false)
  const citationSources = citationCatalog.length > 0 ? citationCatalog : sources
  const citationSourceMap = useMemo(() => buildCitationSourceMap(citationSources), [citationSources])
  const hasVisibleContent = Boolean((content || '').trim())
  const displaySources = showAllSources ? sources : sources.slice(0, 5)
  const markdownContent = useMemo(() => toStructuredMarkdown(hasVisibleContent ? content : ''), [content, hasVisibleContent])

  if (role === 'user') {
    return (
      <div className="mb-4 flex flex-col items-end gap-2">
        <div className="max-w-[82%] rounded-2xl rounded-br-sm bg-ink px-4 py-3 text-sm text-white shadow-[0_8px_24px_rgba(12,10,9,0.12)]">
          {content && <p>{content}</p>}
          {fileName && <p className="mt-1 text-xs opacity-70">附件：{fileName}</p>}
        </div>
        {sensorData && <SensorRadar data={sensorData} />}
      </div>
    )
  }

  return (
    <div className="mb-8">
      <div className="flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[#d6d3d1] bg-white">
          <span className="font-display text-xs text-[#292524]">Bot</span>
        </div>

        <div className="min-w-0 flex-1">
          {streaming && !hasVisibleContent && <LoadingIndicator />}

          {hasVisibleContent && (
            <div className="px-1 py-1">
              <div className="prose max-w-none text-ink
                [&_h1]:mb-3 [&_h1]:mt-2 [&_h1]:text-[1.32rem] [&_h1]:font-bold [&_h1]:leading-[1.35] [&_h1]:tracking-[-0.01em] [&_h1]:text-[#1c1917]
                [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-[1.1rem] [&_h2]:font-semibold [&_h2]:leading-7 [&_h2]:text-[#292524]
                [&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:text-[0.97rem] [&_h3]:font-medium [&_h3]:leading-7 [&_h3]:text-[#57534e]
                [&_p]:my-1.5 [&_p]:text-[0.97rem] [&_p]:leading-7
                [&_strong]:font-semibold [&_ul]:my-3 [&_ul]:pl-6 [&_ol]:my-3 [&_ol]:pl-6
                [&_li]:my-1.5 [&_li]:leading-7">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    del: ({ children }) => <span>{children}</span>,
                    p: ({ children }) => <p>{renderCitationChildren(children, citationSourceMap)}</p>,
                    li: ({ children }) => <li>{renderCitationChildren(children, citationSourceMap)}</li>,
                    th: ({ children }) => (
                      <th className="min-w-[9rem] border border-[#e7e5e4] bg-[#f0efed] px-3 py-3 text-left text-sm font-semibold leading-6 text-[#292524]">
                        {renderCitationChildren(children, citationSourceMap)}
                      </th>
                    ),
                    td: ({ children }) => (
                      <td className="min-w-[9rem] border border-[#e7e5e4] px-3 py-3 align-top text-sm leading-6 text-[#4e4e4e]">
                        {renderCitationChildren(children, citationSourceMap)}
                      </td>
                    ),
                    table: ({ children }) => (
                      <div className="my-5 overflow-x-auto rounded-2xl border border-[#e7e5e4] bg-white">
                        <table className="min-w-full border-collapse">{children}</table>
                      </div>
                    ),
                  }}
                >
                  {markdownContent}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {sources.length > 0 && (
            <div className="mt-5 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs uppercase tracking-[0.12em] text-[#a8a29e]">参考资料</p>
                {sources.length > 5 && (
                  <button
                    type="button"
                    onClick={() => setShowAllSources(value => !value)}
                    className="text-xs font-medium text-[#6b5d52] transition-colors hover:text-[#2f241f]"
                  >
                    {showAllSources ? '收起' : `展开其余 ${sources.length - 5} 条`}
                  </button>
                )}
              </div>
              {displaySources.map(source => <SourceCard key={source.id} source={source} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
