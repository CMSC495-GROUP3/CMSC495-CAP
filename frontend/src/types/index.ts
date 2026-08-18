export interface Message {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  answer: string
  sources: string[]
  confidence: number | null
  follow_ups: string[]
  /** True when retrieval fell below the grounding threshold and no answer was generated. */
  refused: boolean
  session_id: string | null
}

export interface Conversation {
  session_id: string
  title: string
  project_id: string | null
  updated_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
  created_at: string
}

export interface Project {
  project_id: string
  name: string
  created_at: string
}

/** One indexed policy document, as shown in the document library. */
export interface PolicyDocument {
  source: string
  doc_id: string
  title: string
  category: string | null
  owner: string | null
  effective_date: string | null
  passage_count: number
  preview: string
}

export interface DocumentsResponse {
  items: PolicyDocument[]
  total: number
}
