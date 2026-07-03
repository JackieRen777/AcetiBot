import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import ChatMessage from '../components/ChatMessage'
import ChatInput from '../components/ChatInput'
import { parseSensorCSV } from '../utils/sensor'

const WELCOME = {
  id: 0, role: 'assistant',
  content: '你好，我是**智醯**。请描述您的配方需求，或上传电子鼻／舌 CSV 数据文件，我将结合知识库为您提供配方改良建议。',
  sources: [],
}

const API_BASE_URL = import.meta.env.VITE_API_URL
  || (import.meta.env.VITE_API_HOST ? `https://${import.meta.env.VITE_API_HOST}` : 'http://localhost:8013')
const SUGGESTED_PROMPTS = [
  '设计适合江浙消费者的甜醋配方方向',
  '结合电子舌数据，给出酸甜平衡改良建议',
  '镇江香醋与甜醋的酸度标准差异是什么',
  '如果要做更年轻化的米醋口味，工艺上怎么调',
]

export default function Chat() {
  const [messages, setMessages] = useState([WELCOME])
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)
  const nav = useNavigate()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const toSources = (sources = []) => sources.map((s, i) => ({
    id: i + 1,
    title: s.metadata?.source || '未知来源',
    type: s.metadata?.doc_type || '文献',
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
          updateAssistantMessage(messageId, message => ({
            content: `${message.content || ''}${event.content}`,
          }))
        } else if (event.type === 'done') {
          updateAssistantMessage(messageId, () => ({
            sources: toSources(event.sources),
            streaming: false,
          }))
        } else if (event.type === 'error') {
          updateAssistantMessage(messageId, () => ({
            content: event.message,
            sources: [],
            streaming: false,
          }))
          return
        }
      }
    }

    if (buffer.trim()) {
      const event = JSON.parse(buffer)
      if (event.type === 'done') {
        updateAssistantMessage(messageId, () => ({
          sources: toSources(event.sources),
          streaming: false,
        }))
      } else if (event.type === 'error') {
        updateAssistantMessage(messageId, () => ({
          content: event.message,
          sources: [],
          streaming: false,
        }))
      }
    }
  }

  const handleSend = async (text, file) => {
    let sensorData = null
    if (file?.name?.toLowerCase().endsWith('.csv')) {
      sensorData = parseSensorCSV(await file.text())
    }

    setMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      content: text,
      fileName: file?.name,
      sensorData,
    }])

    const assistantId = Date.now() + 1
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      sources: [],
      streaming: true,
    }])

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
            body: JSON.stringify({ question: text }),
          }

      const res = await fetch(`${API_BASE_URL}/query/stream`, requestInit)
      await streamResponse(res, assistantId)
    } catch {
      updateAssistantMessage(assistantId, () => ({
        content: '⚠️ 连接后端失败，请确认 `uvicorn api:app --host 127.0.0.1 --port 8013` 已启动，或设置 `VITE_API_URL` 指向你的实际地址。',
        sources: [],
        streaming: false,
      }))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-screen bg-[#f5f5f5] font-body">
      {/* Header */}
      <header className="shrink-0 flex items-center gap-3 px-6 py-4 bg-white border-b border-[#e7e5e4]">
        <button onClick={() => nav('/')} className="text-[#777169] hover:text-[#292524] transition-colors">
          <ArrowLeft size={18} />
        </button>
        <span className="font-display text-xl font-light tracking-tight text-[#292524]">智醯</span>
        <span className="text-[#a8a29e] text-sm">配方优化助手</span>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto px-4 py-6 max-w-3xl w-full mx-auto">
        {messages.map(msg => <ChatMessage key={msg.id} message={msg} />)}
        {messages.length === 1 && (
          <div className="mt-2 mb-6">
            <p className="text-xs uppercase tracking-[0.12em] text-[#a8a29e] mb-3">演示建议问题</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_PROMPTS.map(prompt => (
                <button
                  key={prompt}
                  onClick={() => handleSend(prompt, null)}
                  disabled={loading}
                  className="rounded-full border border-hairline bg-white px-4 py-2 text-sm text-[#4e4e4e] hover:border-[#292524] hover:text-[#292524] transition-colors disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}
        {loading && !messages.some(message => message.streaming) && (
          <div className="flex gap-1 px-4 py-3">
            {[0,1,2].map(i => (
              <span key={i} className="w-1.5 h-1.5 rounded-full bg-[#a8a29e] animate-bounce"
                style={{ animationDelay: `${i * 0.15}s` }} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      {/* Input */}
      <div className="shrink-0 border-t border-[#e7e5e4] bg-white">
        <div className="max-w-3xl mx-auto">
          <ChatInput onSend={handleSend} loading={loading} />
        </div>
      </div>
    </div>
  )
}
