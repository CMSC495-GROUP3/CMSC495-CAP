import { useState, useEffect, useRef } from 'react'
import client, { TOKEN_KEY } from '../api/client'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number | null
  follow_ups?: string[]
  /** True when the assistant declined because retrieval was too weak to ground an answer. */
  refused?: boolean
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

  useEffect(() => {
    if (!sessionId) {
      setMessages([])
      return
    }
    if (skipNextFetch.current) {
      skipNextFetch.current = false
      return
    }
    client.get(`/api/conversations/${sessionId}`).then((res) => {
      const raw: {
        role: string
        content: string
        sources?: string[]
        confidence?: number | null
        refused?: boolean
      }[] = res.data.messages ?? []
      const mapped: ChatMessage[] = raw.map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        sources: m.sources,
        confidence: m.confidence,
        refused: m.refused,
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
            setMessages((prev) => {
              const updated = [...prev]
              updated[lastAssistantIdx] = { ...updated[lastAssistantIdx], follow_ups: r.data.follow_ups }
              return updated
            })
          }).catch(() => { /* follow-ups are non-critical */ })
        }
      }
    })
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

      const history = messages.map((m) => ({ role: m.role, content: m.content, sources: m.sources ?? [] }))
      const token = localStorage.getItem(TOKEN_KEY) ?? ''

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ question, session_id: sid, chat_history: history }),
      })

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let assistantPushed = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

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

  return { messages, loading, streaming, sendMessage }
}
