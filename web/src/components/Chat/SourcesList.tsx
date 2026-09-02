/**
 * SourcesList — collapsible accordion naming the policy documents an answer was drawn from.
 * Uses Headless UI Disclosure so the open/close state is accessible and animated.
 */
import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react'
import { ChevronRight } from 'lucide-react'

interface Props {
  sources: string[]
}


export default function SourcesList({ sources }: Props) {
  if (!sources.length) return null

  return (
    <Disclosure>
      {({ open }) => (
        <div className="mt-2">
          <DisclosureButton className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors cursor-pointer">
            <ChevronRight
              size={12}
              className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
            />
            {sources.length} source document{sources.length !== 1 ? 's' : ''}
          </DisclosureButton>
          <DisclosurePanel className="mt-1.5 flex flex-wrap gap-1.5">
            {sources.map((src) => (
              <span
                key={src}
                className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-300 border border-white/10"
              >
                {src}
              </span>
            ))}
          </DisclosurePanel>
        </div>
      )}
    </Disclosure>
  )
}
