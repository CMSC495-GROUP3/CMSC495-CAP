/**
 * ChatPage — the main chat interface.
 *
 * Empty state: hero title + 4 clickable starter prompts.
 * Active state: MessageList + ChatInput.
 *
 * session_id lives in the URL (?session_id=uuid). When a new conversation is
 * created (first message sent), useChat calls onSessionCreated which updates the URL.
 */
import { useSearchParams } from 'react-router-dom'
import { APP_NAME, APP_TAGLINE } from '../config'
import { useChat } from '../hooks/useChat'
import MessageList from '../components/Chat/MessageList'
import ChatInput from '../components/Chat/ChatInput'

const STARTER_PROMPTS = [
  'How many PTO days do I get in my first year?',
  'How long do I have to enroll in benefits after starting?',
  'How much does the company match on my 401(k)?',
  'What is the meal limit when travelling for work?',
]

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const sessionId = searchParams.get('session_id')

  const { messages, loading, streaming, sendMessage, markEscalated } = useChat({
    sessionId,
    onSessionCreated: (id) => {
      // Update the URL with the new session_id without re-mounting the component
      setSearchParams({ session_id: id }, { replace: true })
    },
  })

  const hasMessages = messages.length > 0

  return (
    <div className="flex flex-col flex-1 h-screen overflow-hidden">
      {/* Page header — compact once chat starts */}
      <header className="flex-shrink-0 px-6 pt-6 pb-2">
        {!hasMessages && (
          <div className="text-center py-8">
            <h1 className="text-2xl font-semibold text-gray-100 mb-1">{APP_NAME}</h1>
            <p className="text-sm text-gray-500">{APP_TAGLINE}</p>
          </div>
        )}
      </header>

      {/* Empty state starter prompts */}
      {!hasMessages && (
        <div className="flex-1 flex flex-col items-center justify-center px-6 gap-3 pb-24">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
            {STARTER_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                onClick={() => sendMessage(prompt)}
                disabled={loading || streaming}
                className="text-left px-4 py-3 rounded-xl border border-white/10 bg-white/4 hover:bg-white/8 text-sm text-gray-300 hover:text-white transition-colors cursor-pointer disabled:opacity-50"
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Message list */}
      {hasMessages && (
        <MessageList
          messages={messages}
          sessionId={sessionId}
          loading={loading}
          onFollowUp={sendMessage}
          onEscalated={markEscalated}
        />
      )}

      {/* Input */}
      <div className="flex-shrink-0">
        <ChatInput onSend={sendMessage} disabled={loading || streaming} />
      </div>
    </div>
  )
}
