import { useState, useRef } from 'react'
import { Paperclip, Send, X } from 'lucide-react'

export default function ChatInput({ onSend, loading }) {
  const [text, setText] = useState('')
  const [file, setFile] = useState(null)
  const fileRef = useRef(null)

  const submit = () => {
    if ((!text.trim() && !file) || loading) return
    onSend(text.trim(), file)
    setText('')
    setFile(null)
  }

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }

  return (
    <div className="p-4 space-y-2">
      {/* File badge */}
      {file && (
        <div className="flex items-center gap-2 text-sm text-ink-soft bg-[#f0efed] rounded-lg px-3 py-1.5 w-fit">
          <Paperclip size={13} />
          <span>{file.name}</span>
          <button onClick={() => setFile(null)} className="hover:text-ink"><X size={13} /></button>
        </div>
      )}

      <div className="flex items-end gap-2 bg-surface border border-hairline rounded-xl px-4 py-3 focus-within:border-ink transition-colors">
        {/* File upload */}
        <button onClick={() => fileRef.current?.click()}
          className="text-muted hover:text-ink transition-colors mb-0.5">
          <Paperclip size={18} />
        </button>
        <input ref={fileRef} type="file" accept=".csv,.pdf,.xlsx,.xls"
          className="hidden" onChange={e => setFile(e.target.files[0])} />

        {/* Textarea */}
        <textarea
          className="flex-1 resize-none bg-transparent outline-none text-sm text-ink placeholder-muted max-h-40 leading-relaxed"
          placeholder="描述您的配方需求，或询问工艺问题…"
          rows={1}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKey}
          style={{ height: 'auto' }}
          onInput={e => { e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px' }}
        />

        {/* Send */}
        <button onClick={submit} disabled={loading || (!text.trim() && !file)}
          className="mb-0.5 p-1.5 rounded-lg bg-ink text-white disabled:opacity-30 hover:bg-[#0c0a09] transition-colors">
          <Send size={15} />
        </button>
      </div>
      <p className="text-xs text-muted text-center">支持上传 CSV（感官数据）· PDF · Excel</p>
    </div>
  )
}
