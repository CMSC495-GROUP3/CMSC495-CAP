import { useState, useCallback } from 'react'
import client from '../api/client'
import type { Conversation } from '../types'

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(false)

  const fetchConversations = useCallback(async () => {
    setLoading(true)
    try {
      const res = await client.get('/api/conversations')
      setConversations(res.data)
    } finally {
      setLoading(false)
    }
  }, [])

  async function createConversation(title: string, projectId?: string): Promise<Conversation> {
    const res = await client.post('/api/conversations', {
      title,
      project_id: projectId ?? null,
    })
    setConversations((prev) => [res.data, ...prev])
    return res.data
  }

  async function renameConversation(sessionId: string, title: string) {
    await client.patch(`/api/conversations/${sessionId}`, { title })
    setConversations((prev) =>
      prev.map((c) => (c.session_id === sessionId ? { ...c, title } : c))
    )
  }

  async function assignToProject(sessionId: string, projectId: string | null) {
    // Explicitly send project_id (even when null) so the backend knows to update it.
    // The backend uses model_fields_set to distinguish "set to null" from "not sent".
    await client.patch(`/api/conversations/${sessionId}`, { project_id: projectId })
    setConversations((prev) =>
      prev.map((c) => (c.session_id === sessionId ? { ...c, project_id: projectId } : c))
    )
  }

  async function deleteConversation(sessionId: string) {
    await client.delete(`/api/conversations/${sessionId}`)
    setConversations((prev) => prev.filter((c) => c.session_id !== sessionId))
  }

  return {
    conversations,
    loading,
    fetchConversations,
    createConversation,
    renameConversation,
    assignToProject,
    deleteConversation,
  }
}
