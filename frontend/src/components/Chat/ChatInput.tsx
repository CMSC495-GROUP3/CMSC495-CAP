import { useState, useRef, useEffect } from 'react'
import { ArrowUp } from 'lucide-react'

interface Props {
  onSend: (message: string) => void
  disabled: boolean
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`
  }, [value])

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="px-4 pb-4 pt-2">
      <div className="flex items-center gap-2 rounded-2xl bg-white/5 border border-white/10 px-4 py-3 focus-within:border-[#C2B067]/30 transition-colors">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask a question…"
          rows={1}
          className="flex-1 bg-transparent text-sm text-gray-200 placeholder-gray-500 resize-none outline-none leading-relaxed disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={disabled || !value.trim()}
          className="flex-shrink-0 w-8 h-8 rounded-xl bg-gradient-to-br from-[#A08340] to-[#C2B067] hover:from-[#B09450] hover:to-[#D4C278] disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center transition-all cursor-pointer"
        >
          <ArrowUp size={16} className="text-[#0e0e0e]" />
        </button>
      </div>
      <p className="text-center text-[11px] text-gray-600 mt-2">
        Shift+Enter for new line · answers are drawn only from indexed policy documents
      </p>
    </div>
  )
}
