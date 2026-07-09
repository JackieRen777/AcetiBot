import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import ChatMessage from '../components/ChatMessage'
import ChatInput from '../components/ChatInput'
import { parseSensorCSV } from '../utils/sensor'

const WELCOME = {
  id: 0,
  role: 'assistant',
  content: 'Welcome to AcetiBot！我是智醯，专注食醋配方、工艺优化、风味调配与合规标准问题。每一条回答都来自工艺论文、国家标准或企业专利，有据可查。把你的问题告诉我吧 →',
  sources: [],
  citationCatalog: [],
}

const API_BASE_URL = import.meta.env.VITE_API_URL
  || (import.meta.env.VITE_API_HOST ? `https://${import.meta.env.VITE_API_HOST}` : 'http://localhost:8013')

const SUGGESTED_PROMPTS = [
  '镇江香醋的总酸最低限值是多少？',
  '固态发酵与液态发酵对食醋风味影响有何差异？',
  '设计一款适合年轻消费者的低酸米醋配方，并说明工艺调整依据',
]

const MAX_HISTORY_TURNS = 7
const MAX_HISTORY_CHARS = 3000

// 构建多轮对话历史，最近 N 轮，总字符不超过上限
function buildConversationHistory(messages, maxTurns, maxChars) {
  const pairs = []
  // 跳过 id=0 的欢迎消息，从真实对话开始收集 user+assistant 对
  const convo = messages.filter(m => m.id !== 0 && (m.role === 'user' || m.role === 'assistant'))
  for (let i = 0; i < convo.length - 1; i++) {
    if (convo[i].role === 'user' && convo[i + 1]?.role === 'assistant') {
      pairs.push({ user: convo[i].content || '', assistant: convo[i + 1].content || '' })
      i++ // 跳过已消费的 assistant
    }
  }
  // 取最近 maxTurns 轮，截断超长内容
  const recent = pairs.slice(-maxTurns)
  const history = []
  let totalChars = 0
  for (const pair of recent) {
    const userText = pair.user.slice(0, 500)
    const assistantText = pair.assistant.slice(0, 1000)
    totalChars += userText.length + assistantText.length
    if (totalChars > maxChars) break
    history.push({ role: 'user', content: userText })
    history.push({ role: 'assistant', content: assistantText })
  }
  return history
}

export default function Chat() {
  const [messages, setMessages] = useState([WELCOME])
  const [loading, setLoading] = useState(false)
  const [isUserScrolled, setIsUserScrolled] = useState(false)
  const bottomRef = useRef(null)
  const scrollContainerRef = useRef(null)
  const nav = useNavigate()

  // 智能滚动：监听用户是否主动上滑
  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const handleScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
      setIsUserScrolled(!nearBottom)
    }
    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [])

  // 自动跟随：仅在未被用户接管时执行
  useEffect(() => {
    if (!isUserScrolled) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, loading, isUserScrolled])

  const toSources = (sources = []) => sources.map((s, i) => ({
    id: s.metadata?.display_id || s.metadata?.citation_id || i + 1,
    displayId: s.metadata?.display_id || s.metadata?.citation_id || i + 1,
    rawCitationId: s.metadata?.raw_citation_id,
    rawCitationIds: s.metadata?.raw_citation_ids || [],
    citedPages: s.metadata?.cited_pages || [],
    citedPageLabels: s.metadata?.cited_page_labels || [],
    title: s.metadata?.display_title || s.metadata?.source || '未知来源',
    type: s.metadata?.display_category || s.metadata?.doc_type || '文献',
    author: s.metadata?.display_author,
    year: s.metadata?.year || s.metadata?.display_time,
    ...s.metadata,
    text: s.text,
  }))

  const updateAssistantMessage = (messageId, updater) => {
    setMessages(prev => prev.map(message => (
      message.id === messageId
        ? { ...message, ...updater(message) }
        : message
    )))
  }

  const streamResponse = async (response, messageId) => {
    if (!response.ok || !response.body) {
      throw new Error('stream unavailable')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let sawDelta = false

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.trim()) continue
        const event = JSON.parse(line)

        if (event.type === 'delta') {
          sawDelta = true
          updateAssistantMessage(messageId, message => ({
            content: `${message.content || ''}${event.content}`,
            loadingLabel: '生成回答中',
          }))
        } else if (event.type === 'catalog') {
          updateAssistantMessage(messageId, () => ({
            citationCatalog: toSources(event.sources),
          }))
        } else if (event.type === 'replace') {
          updateAssistantMessage(messageId, () => ({
            content: event.content || '',
            loadingLabel: '生成回答中',
          }))
        } else if (event.type === 'done') {
          updateAssistantMessage(messageId, message => ({
            content: message.content || event.answer || '',
            sources: toSources(event.sources),
            citationCatalog: toSources(event.citation_catalog || event.sources),
            streaming: false,
            loadingLabel: null,
          }))
        } else if (event.type === 'error') {
          updateAssistantMessage(messageId, () => ({
            content: event.message,
            sources: [],
            citationCatalog: [],
            streaming: false,
            loadingLabel: null,
          }))
          return
        }
      }
    }

    if (!sawDelta) {
      updateAssistantMessage(messageId, () => ({
        loadingLabel: '生成回答中',
      }))
    }

    if (buffer.trim()) {
      const event = JSON.parse(buffer)
      if (event.type === 'done') {
        updateAssistantMessage(messageId, message => ({
          content: message.content || event.answer || '',
          sources: toSources(event.sources),
          citationCatalog: toSources(event.citation_catalog || event.sources),
          streaming: false,
          loadingLabel: null,
        }))
      } else if (event.type === 'replace') {
        updateAssistantMessage(messageId, () => ({
          content: event.content || '',
          loadingLabel: '生成回答中',
        }))
      } else if (event.type === 'error') {
        updateAssistantMessage(messageId, () => ({
          content: event.message,
          sources: [],
          citationCatalog: [],
          streaming: false,
          loadingLabel: null,
        }))
      }
    }
  }

  const handleSend = async (text, file) => {
    // 发送新消息时恢复自动跟随
    setIsUserScrolled(false)

    let sensorData = null
    if (file?.name?.toLowerCase().endsWith('.csv')) {
      sensorData = parseSensorCSV(await file.text())
    }

    const userId = Date.now()
    const assistantId = userId + 1

    // 构建多轮对话历史（发送前取当前 messages 快照）
    const conversationHistory = buildConversationHistory(messages, MAX_HISTORY_TURNS, MAX_HISTORY_CHARS)

    setMessages(prev => [
      ...prev,
      {
        id: userId,
        role: 'user',
        content: text,
        fileName: file?.name,
        sensorData,
      },
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        sources: [],
        citationCatalog: [],
        streaming: true,
        loadingLabel: '资料整理中',
      },
    ])

    setLoading(true)
    try {
      const requestInit = file
        ? (() => {
            const formData = new FormData()
            formData.append('question', text)
            formData.append('file', file)
            return { method: 'POST', body: formData }
          })()
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              question: text,
              conversation_history: conversationHistory.length > 0 ? conversationHistory : undefined,
            }),
          }

      const res = await fetch(`${API_BASE_URL}/query/stream`, requestInit)
      await streamResponse(res, assistantId)
    } catch {
      updateAssistantMessage(assistantId, () => ({
        content: '当前问答服务暂时不可用，请稍后重试。',
        sources: [],
        citationCatalog: [],
        streaming: false,
        loadingLabel: null,
      }))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-screen flex-col bg-[#f5f5f5] font-body">
      <header className="shrink-0 border-b border-[#e7e5e4] bg-white">
        <div className="mx-auto flex max-w-4xl items-center gap-3 px-6 py-4">
          <button onClick={() => nav('/')} className="text-[#777169] transition-colors hover:text-[#292524]">
            <ArrowLeft size={18} />
          </button>
          <div className="min-w-0">
            <span className="font-display text-xl font-light tracking-tight text-[#292524]">AcetiBot</span>
          </div>
        </div>
      </header>

      {/* 全宽滚动容器 — tooltip 不受列宽裁剪 */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
        <main className="mx-auto max-w-4xl px-4 py-6 w-full">
          {messages.map(msg => <ChatMessage key={msg.id} message={msg} />)}

          {messages.length === 1 && (
            <div className="mb-8 mt-2">
              <div>
                <p className="mb-3 text-xs uppercase tracking-[0.12em] text-[#a8a29e]">演示建议问题</p>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTED_PROMPTS.map(prompt => (
                    <button
                      key={prompt}
                      onClick={() => handleSend(prompt, null)}
                      disabled={loading}
                      className="rounded-full border border-[#e7e5e4] bg-white px-4 py-2 text-sm text-[#4e4e4e] transition-colors hover:border-[#292524] hover:text-[#292524] disabled:opacity-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </main>
      </div>

      <div className="shrink-0 border-t border-[#e7e5e4] bg-white">
        <div className="mx-auto max-w-4xl">
          <ChatInput onSend={handleSend} loading={loading} helperText="支持补充 PDF / Excel / CSV 资料" />
        </div>
      </div>
    </div>
  )
}
