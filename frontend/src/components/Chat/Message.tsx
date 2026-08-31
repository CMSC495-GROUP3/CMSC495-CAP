import ReactMarkdown from 'react-markdown'
import { AlertCircle } from 'lucide-react'
import ConfidenceBadge from './ConfidenceBadge'
import SourcesList from './SourcesList'
import FollowUpButtons from './FollowUpButtons'
import EscalateButton from './EscalateButton'
import type { ChatMessage } from '../../hooks/useChat'

interface Props {
  message: ChatMessage
  /** Position in the conversation; the escalation request names the turn by it. */
  index: number
  sessionId: string | null
  isLast: boolean
  onFollowUp: (q: string) => void
  onEscalated: (index: number, escalationId: string) => void
}

export default function Message({ message, index, sessionId, isLast, onFollowUp, onEscalated }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] px-4 py-2.5 rounded-2xl rounded-br-sm bg-[#C2B067]/15 border border-[#C2B067]/20 text-gray-100 text-sm">
          {message.content}
        </div>
      </div>
    )
  }

  if (message.refused) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%] space-y-2">
          <div className="flex gap-2.5 px-4 py-3 rounded-2xl rounded-bl-sm bg-amber-500/8 border border-amber-500/25 text-sm text-gray-200">
            <AlertCircle size={15} className="flex-shrink-0 text-amber-400/90 mt-0.5" />
            <div>
              <p className="leading-relaxed">{message.content}</p>
              <p className="text-xs text-gray-500 mt-2">
                No indexed policy matched your question closely enough to answer from.
                Check the Policy Library to see what is currently loaded.
              </p>
            </div>
          </div>
          <div className="px-1 flex flex-wrap items-center gap-x-4 gap-y-2">
            <ConfidenceBadge confidence={message.confidence} />
            <EscalateButton
              sessionId={sessionId}
              messageIndex={index}
              reason="refused"
              escalationId={message.escalation_id}
              prominent
              onEscalated={(id) => onEscalated(index, id)}
            />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <div className="px-4 py-3 rounded-2xl rounded-bl-sm bg-white/5 border border-white/8 text-sm
          prose prose-invert prose-sm max-w-none
          prose-p:text-gray-200 prose-p:my-1.5 prose-p:leading-relaxed
          prose-ul:text-gray-200 prose-ul:my-1.5 prose-ul:pl-4
          prose-ol:text-gray-200 prose-ol:my-1.5 prose-ol:pl-4
          prose-li:text-gray-200 prose-li:my-0.5 prose-li:marker:text-gray-500
          prose-strong:text-gray-100 prose-strong:font-semibold
          prose-em:text-gray-300
          prose-code:text-[#C2B067] prose-code:bg-white/8 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:before:content-none prose-code:after:content-none
          prose-pre:bg-white/8 prose-pre:border prose-pre:border-white/10 prose-pre:rounded-xl prose-pre:text-xs
          prose-h1:text-gray-100 prose-h1:text-base prose-h1:font-semibold prose-h1:mt-3 prose-h1:mb-1
          prose-h2:text-gray-100 prose-h2:text-sm prose-h2:font-semibold prose-h2:mt-3 prose-h2:mb-1
          prose-h3:text-gray-200 prose-h3:text-sm prose-h3:font-medium prose-h3:mt-2 prose-h3:mb-1
          prose-blockquote:border-l-[#C2B067]/50 prose-blockquote:text-gray-400 prose-blockquote:not-italic
          prose-hr:border-white/10
        ">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
        <div className="px-1 space-y-2">
          <ConfidenceBadge confidence={message.confidence} />
          <SourcesList sources={message.sources ?? []} />
          {isLast && (
            <FollowUpButtons questions={message.follow_ups ?? []} onSelect={onFollowUp} />
          )}
          <EscalateButton
            sessionId={sessionId}
            messageIndex={index}
            reason="unhelpful"
            escalationId={message.escalation_id}
            onEscalated={(id) => onEscalated(index, id)}
          />
        </div>
      </div>
    </div>
  )
}
