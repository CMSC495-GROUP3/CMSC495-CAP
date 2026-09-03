/**
 * DocumentCard — one policy document in the library, expandable to read the
 * exact passages that were indexed.
 *
 * Showing stored passages rather than re-rendering the original file is
 * deliberate: this is the audit trail for a citation, so it should display what
 * retrieval actually sees.
 */
import { useState } from 'react'
import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react'
import { ChevronRight, FileText } from 'lucide-react'
import client from '../../api/client'
import type { PolicyDocument } from '../../types'

interface Props {
  document: PolicyDocument
}

export default function DocumentCard({ document }: Props) {
  const [passages, setPassages] = useState<string[]>([])
  const [fetched, setFetched] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleOpen(open: boolean) {
    if (!open || fetched) return
    setLoading(true)
    setError('')
    try {
      const res = await client.get<string[]>('/api/documents/passages', {
        params: { source: document.source },
      })
      setPassages(res.data)
      setFetched(true)
    } catch {
      setError('Could not load this document.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Disclosure>
      {({ open }) => (
        <div
          className={`rounded-xl border transition-colors ${
            open ? 'border-[#C2B067]/30 bg-white/5' : 'border-white/8 bg-white/3 hover:bg-white/5'
          }`}
        >
          <DisclosureButton
            onClick={() => handleOpen(!open)}
            className="w-full flex items-start gap-3 px-4 py-3 text-left cursor-pointer"
          >
            <FileText size={15} className="flex-shrink-0 text-[#C2B067] opacity-80 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2 flex-wrap">
                <p className="text-sm font-medium text-gray-200 truncate">{document.title}</p>
                {document.category && (
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#C2B067]/12 text-[#C2B067] border border-[#C2B067]/20">
                    {document.category}
                  </span>
                )}
              </div>
              {!open && document.preview && (
                <p className="text-xs text-gray-500 mt-1 line-clamp-2 leading-relaxed">
                  {document.preview}
                </p>
              )}
              <p className="text-[11px] text-gray-600 mt-1">
                {document.passage_count} indexed passage{document.passage_count !== 1 ? 's' : ''}
                {document.effective_date && ` · effective ${document.effective_date}`}
                {document.owner && ` · ${document.owner}`}
              </p>
            </div>
            <ChevronRight
              size={14}
              className={`flex-shrink-0 text-gray-500 transition-transform duration-150 mt-0.5 ${
                open ? 'rotate-90' : ''
              }`}
            />
          </DisclosureButton>

          <DisclosurePanel className="px-4 pb-4">
            {loading && <p className="text-xs text-gray-500 py-2">Loading…</p>}
            {error && <p className="text-xs text-red-400 py-2">{error}</p>}
            {!loading && !error && passages.length > 0 && (
              <div className="space-y-4 pt-1 max-h-96 overflow-y-auto pr-1">
                {passages.map((passage, i) => (
                  <div key={i} className="relative pl-3">
                    <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-full bg-gradient-to-b from-[#A08340] to-[#C2B067] opacity-40" />
                    <p className="text-xs text-gray-500 mb-1">Passage {i + 1}</p>
                    <p className="text-xs text-gray-400 leading-relaxed whitespace-pre-line">
                      {passage}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </DisclosurePanel>
        </div>
      )}
    </Disclosure>
  )
}
