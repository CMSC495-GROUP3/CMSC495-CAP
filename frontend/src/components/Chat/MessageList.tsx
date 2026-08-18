/**
 * MessageList — scrollable list of chat messages.
 * Auto-scrolls to the bottom when a new message arrives.
 * Shows a pulsing dot while the assistant is thinking.
 */
import { useEffect, useRef } from 'react'
import Message from './Message'
import type { ChatMessage } from '../../hooks/useChat'

interface Props {
  messages: ChatMessage[]
  loading: boolean
  onFollowUp: (q: string) => void
}

export default function MessageList({ messages, loading, onFollowUp }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
      {messages.map((msg, i) => (
        <Message
          key={i}
          message={msg}
          isLast={i === messages.length - 1 && msg.role === 'assistant'}
          onFollowUp={onFollowUp}
        />
      ))}

      {loading && (
        <div className="flex justify-start">
          <div className="px-4 py-3 rounded-2xl rounded-bl-sm bg-white/5 border border-white/8">
            <div className="flex gap-1.5 items-center h-4">
              <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:0ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:300ms]" />
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
