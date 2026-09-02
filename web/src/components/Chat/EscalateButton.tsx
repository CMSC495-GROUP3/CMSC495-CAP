/**
 * EscalateButton — hands a question to a person.
 *
 * Rendered under every assistant turn. On a refusal it is the main call to
 * action; under an answer it is a quiet "not what you needed?" link. Both post
 * the same request, and `reason` records which one was used.
 *
 * The request names the message by its position in the stored conversation and
 * the server copies the question from its own record. The client never sends
 * the text, so it cannot escalate an exchange that did not happen.
 */
import { useState } from 'react'
import { AxiosError } from 'axios'
import { Check, LifeBuoy } from 'lucide-react'
import client from '../../api/client'
import { ESCALATION_CONTACT } from '../../config'
import type { Escalation, EscalationReason } from '../../types'

interface Props {
  sessionId: string | null
  messageIndex: number
  reason: EscalationReason
  /** Set once this message has been escalated, in this session or a previous one. */
  escalationId?: string
  /** Full button rather than a text link. Used on the refusal card. */
  prominent?: boolean
  onEscalated: (escalationId: string) => void
}

type Phase = 'idle' | 'composing' | 'sending' | 'error'

/** Characters of the id shown to the employee, enough to quote back to HR. */
const REFERENCE_LENGTH = 8
/** Mirrors ESCALATION_NOTE_MAX_LENGTH in policy_assistant/rag/config.py; the server enforces the real limit. */
const NOTE_MAX_LENGTH = 2000

function explain(error: unknown): string {
  const status = error instanceof AxiosError ? error.response?.status : undefined
  if (status === 400) return 'This conversation is out of sync. Reload the page and try again.'
  if (status === 429) return 'Too many requests. Wait a minute and try again.'
  return `Could not reach ${ESCALATION_CONTACT} right now. Try again in a moment.`
}

export default function EscalateButton({
  sessionId, messageIndex, reason, escalationId, prominent = false, onEscalated,
}: Props) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  if (escalationId) {
    return (
      <p className="inline-flex items-center gap-1.5 text-xs text-gray-400">
        <Check size={12} className="text-green-400" />
        Sent to {ESCALATION_CONTACT} · ref {escalationId.slice(0, REFERENCE_LENGTH)}
      </p>
    )
  }

  // Every message in the UI belongs to a stored conversation, so this only
  // guards the moment before the first conversation exists.
  if (!sessionId) return null

  async function submit() {
    setPhase('sending')
    setError('')
    try {
      const res = await client.post<Escalation>('/api/escalations', {
        session_id: sessionId,
        message_index: messageIndex,
        reason,
        note: note.trim() || null,
      })
      onEscalated(res.data.escalation_id)
    } catch (err) {
      setError(explain(err))
      setPhase('error')
    }
  }

  if (phase === 'idle') {
    return prominent ? (
      <button
        type="button"
        onClick={() => setPhase('composing')}
        className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20 hover:border-amber-500/50 transition-colors cursor-pointer"
      >
        <LifeBuoy size={13} />
        Ask {ESCALATION_CONTACT}
      </button>
    ) : (
      <button
        type="button"
        onClick={() => setPhase('composing')}
        className="text-xs text-gray-500 hover:text-gray-300 transition-colors cursor-pointer"
      >
        Not what you needed? Ask {ESCALATION_CONTACT}
      </button>
    )
  }

  const sending = phase === 'sending'

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); void submit() }}
      className="mt-1 w-full max-w-md space-y-2 rounded-xl border border-white/10 bg-white/4 p-3"
    >
      <label className="block text-xs text-gray-400" htmlFor={`escalation-note-${messageIndex}`}>
        Send this question to {ESCALATION_CONTACT}. Add context if it helps (optional).
      </label>
      <textarea
        id={`escalation-note-${messageIndex}`}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={NOTE_MAX_LENGTH}
        rows={3}
        disabled={sending}
        placeholder="For example: my manager said this changed last quarter."
        className="w-full resize-y rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:border-[#C2B067]/50 focus:outline-none disabled:opacity-60"
      />
      {phase === 'error' && (
        <p role="alert" className="text-xs text-red-300">{error}</p>
      )}
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={sending}
          className="text-xs px-3 py-1.5 rounded-lg bg-[#C2B067]/20 border border-[#C2B067]/40 text-gray-100 hover:bg-[#C2B067]/30 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-default"
        >
          {sending ? 'Sending…' : phase === 'error' ? 'Try again' : 'Send'}
        </button>
        <button
          type="button"
          disabled={sending}
          onClick={() => { setPhase('idle'); setNote(''); setError('') }}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors cursor-pointer disabled:opacity-60"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
