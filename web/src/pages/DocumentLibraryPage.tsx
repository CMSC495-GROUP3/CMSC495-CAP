/**
 * DocumentLibraryPage — searchable index of every document the assistant can
 * answer from.
 *
 * This exists so employees can tell the difference between "the assistant won't
 * answer that" and "that policy isn't loaded yet". Search and pagination are
 * server-side (?q=&category=&limit=&skip=) so this holds up as the corpus grows.
 */
import { useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import client from '../api/client'
import DocumentCard from '../components/Documents/DocumentCard'
import type { PolicyDocument, DocumentsResponse } from '../types'

const LIMIT = 50
const DEBOUNCE_MS = 300

export default function DocumentLibraryPage() {
  const [documents, setDocuments] = useState<PolicyDocument[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [activeCategory, setActiveCategory] = useState('')
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function fetchDocuments(q: string, category: string) {
    client
      .get<DocumentsResponse>('/api/documents', {
        params: { q, category, limit: LIMIT, skip: 0 },
      })
      .then((res) => {
        setDocuments(res.data.items)
        setTotal(res.data.total)
        setError('')
      })
      .catch(() => setError('Could not load the document library.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchDocuments('', '')
    client
      .get<string[]>('/api/documents/categories')
      .then((res) => setCategories(res.data))
      .catch(() => { /* filters are optional — the list still works without them */ })
  }, [])

  // Clear the pending debounce on unmount so a late timer can't fire into a
  // component that no longer exists.
  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }, [])

  function handleSearch(value: string) {
    setQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setLoading(true)
      fetchDocuments(value, activeCategory)
    }, DEBOUNCE_MS)
  }

  function handleCategory(category: string) {
    const next = category === activeCategory ? '' : category
    setActiveCategory(next)
    setLoading(true)
    fetchDocuments(query, next)
  }

  const subtitle = loading
    ? 'Loading…'
    : query.trim() || activeCategory
    ? `${total} matching document${total !== 1 ? 's' : ''}`
    : `${total} document${total !== 1 ? 's' : ''} indexed`

  return (
    <div className="flex-1 overflow-y-auto px-6 py-8">
      <h1 className="text-xl font-semibold text-gray-100 mb-1">Policy Library</h1>
      <p className="text-sm text-gray-500 mb-6">{subtitle}</p>

      <div className="relative mb-4">
        <Search
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none"
        />
        <input
          type="text"
          placeholder="Search policy documents…"
          value={query}
          onChange={(e) => handleSearch(e.target.value)}
          className="w-full pl-8 pr-4 py-2 rounded-xl bg-white/5 border border-white/10 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-[#C2B067]/40 focus:bg-white/7 transition-colors"
        />
      </div>

      {categories.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-5">
          {categories.map((category) => {
            const active = category === activeCategory
            return (
              <button
                key={category}
                onClick={() => handleCategory(category)}
                aria-pressed={active}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors cursor-pointer ${
                  active
                    ? 'bg-[#C2B067]/20 border-[#C2B067]/40 text-[#E8D196]'
                    : 'bg-white/4 border-white/10 text-gray-400 hover:text-gray-200 hover:bg-white/8'
                }`}
              >
                {category}
              </button>
            )
          })}
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!error && !loading && documents.length === 0 ? (
        <p className="text-sm text-gray-500">
          {query.trim() ? `No documents match "${query}"` : 'No documents indexed yet.'}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {documents.map((doc) => (
            <DocumentCard key={doc.source} document={doc} />
          ))}
        </div>
      )}
    </div>
  )
}
