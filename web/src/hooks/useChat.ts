import { useState, useEffect, useRef } from 'react'
import client, { TOKEN_KEY, signOut } from '../api/client'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number | null
  follow_ups?: string[]
  /** True when the assistant declined because retrieval was too weak to ground an answer. */
  refused?: boolean
  /** Set once this turn has been handed to a person. See EscalateButton. */
  escalation_id?: string
}

interface UseChatOptions {
  sessionId: string | null
  onSessionCreated: (sessionId: string) => void
}

export function useChat({ sessionId, onSessionCreated }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)    // true = waiting for first token (show dots)
  const [streaming, setStreaming] = useState(false) // true = tokens arriving (input disabled)
  const activeSessionId = useRef<string | null>(sessionId)
  const skipNextFetch = useRef(false)

  useEffect(() => {
    activeSessionId.current = sessionId
  }, [sessionId])

  // Leaving a conversation (sessionId becomes null) empties the list during
  // this render rather than in an effect, so there is no frame showing the old
  // messages and no cascading re-render. This is React's "adjust state when a
  // prop changes" pattern; the rendered id is tracked so it runs once per change.
  const [renderedSessionId, setRenderedSessionId] = useState(sessionId)
  if (sessionId !== renderedSessionId) {
    setRenderedSessionId(sessionId)
    if (!sessionId) setMessages([])
  }

  // Both requests below can outlive the conversation that started them: the
  // user clicks a second chat before the first has loaded, or "New chat"
  // before follow-ups return. A late response used to write into whichever
  // list was on screen, at an index from the old conversation. Past the end
  // of a shorter list that left a hole, the next `[...prev]` turned the hole
  // into an undefined message, and <Message> crashed on `message.role`, which
  // unmounted the whole app (issue #82). The cleanup flag drops any response
  // that belongs to a conversation the user has already left.
  useEffect(() => {
    if (!sessionId) return
    if (skipNextFetch.current) {
      skipNextFetch.current = false
      return
    }
    let cancelled = false
    client.get(`/api/conversations/${sessionId}`).then((res) => {
      if (cancelled) return
      const raw: {
        role: string
        content: string
        sources?: string[]
        confidence?: number | null
        refused?: boolean
        escalation_id?: string
      }[] = res.data.messages ?? []
      const mapped: ChatMessage[] = raw.map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        sources: m.sources,
        confidence: m.confidence,
        refused: m.refused,
        escalation_id: m.escalation_id,
      }))
      setMessages(mapped)

      // Generate fresh follow-ups for the last Q/A exchange
      const lastAssistantIdx = mapped.reduceRight((found, m, i) => found === -1 && m.role === 'assistant' ? i : found, -1)
      if (lastAssistantIdx > 0) {
        const lastUserIdx = mapped.slice(0, lastAssistantIdx).reduceRight((found, m, i) => found === -1 && m.role === 'user' ? i : found, -1)
        if (lastUserIdx !== -1) {
          client.post('/api/chat/follow-ups', {
            question: mapped[lastUserIdx].content,
            answer: mapped[lastAssistantIdx].content,
          }).then((r) => {
            if (cancelled) return
            setMessages((prev) => {
              // Never write past the end of the list or onto a user turn.
              if (prev[lastAssistantIdx]?.role !== 'assistant') return prev
              const updated = [...prev]
              updated[lastAssistantIdx] = { ...updated[lastAssistantIdx], follow_ups: r.data.follow_ups }
              return updated
            })
          }).catch(() => { /* follow-ups are non-critical */ })
        }
      }
    })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  async function sendMessage(question: string) {
    if (loading || streaming) return

    setLoading(true)
    setMessages((prev) => [...prev, { role: 'user', content: question }])

    try {
      // Create conversation on first message
      let sid = activeSessionId.current
      if (!sid) {
        const res = await client.post('/api/conversations', { title: question.slice(0, 60) })
        sid = res.data.session_id as string
        activeSessionId.current = sid
        skipNextFetch.current = true
        onSessionCreated(sid)
      }

      const token = localStorage.getItem(TOKEN_KEY) ?? ''

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        // History is deliberately NOT sent. The server reads it from the
        // conversation record, so a client cannot inject forged turns into the
        // prompt. See the module docstring in policy_assistant/api/routes/chat.py.
        body: JSON.stringify({ question, session_id: sid }),
      })

      if (!response.ok || !response.body) {
        // Stream uses raw fetch, so it bypasses the axios 401 interceptor.
        // An expired token must sign the user out, not look like a chat error.
        if (response.status === 401) {
          signOut()
          return
        }
        throw new Error(`HTTP ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let assistantPushed = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        // The user opened another conversation or "New chat" mid-stream.
        // Stop rendering so chunks do not land in the wrong list (or on an
        // empty one), but keep reading: closing the connection would make the
        // server persist a fragment, and draining lets it save the whole
        // answer for when the user comes back.
        if (activeSessionId.current !== sid) {
          while (!(await reader.read()).done) { /* discard */ }
          return
        }

        // Decode incrementally; buffer handles chunks that split across SSE boundaries
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? '' // last element may be an incomplete line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (!payload) continue

          let data: Record<string, unknown>
          try { data = JSON.parse(payload) } catch { continue }

          if (data.chunk) {
            if (!assistantPushed) {
              // First token: swap loading dots for the assistant bubble in one update
              assistantPushed = true
              setLoading(false)
              setStreaming(true)
              setMessages((prev) => [...prev, { role: 'assistant', content: data.chunk as string }])
            } else {
              // Append subsequent tokens to the last message
              setMessages((prev) => {
                const last = prev[prev.length - 1]
                return [
                  ...prev.slice(0, -1),
                  { ...last, content: last.content + (data.chunk as string) },
                ]
              })
            }
          } else if (data.done) {
            // Attach sources + confidence immediately; follow_ups arrive in the next event
            setMessages((prev) => {
              const last = prev[prev.length - 1]
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  sources: data.sources as string[],
                  confidence: data.confidence as number | null,
                  refused: Boolean(data.refused),
                },
              ]
            })
          } else if (data.follow_ups) {
            setMessages((prev) => {
              const last = prev[prev.length - 1]
              return [
                ...prev.slice(0, -1),
                { ...last, follow_ups: data.follow_ups as string[] },
              ]
            })
          } else if (data.error) {
            setLoading(false)
            if (assistantPushed) {
              setMessages((prev) => {
                const last = prev[prev.length - 1]
                return [...prev.slice(0, -1), { ...last, content: data.error as string }]
              })
            } else {
              setMessages((prev) => [...prev, { role: 'assistant', content: data.error as string }])
            }
          }
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Sorry, something went wrong. Please try again.' },
      ])
    } finally {
      setLoading(false)
      setStreaming(false)
    }
  }

  /** Record that a message was escalated, so the button shows its reference. */
  function markEscalated(index: number, escalationId: string) {
    setMessages((prev) =>
      prev.map((m, i) => (i === index ? { ...m, escalation_id: escalationId } : m))
    )
  }

  return { messages, loading, streaming, sendMessage, markEscalated }
}
