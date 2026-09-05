import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'

interface Props {
  open: boolean
  onToggle: () => void
}

/** The one button that opens or closes the sidebar; the sidebar's id is its target. */
export default function SidebarToggle({ open, onToggle }: Props) {
  const Icon = open ? PanelLeftClose : PanelLeftOpen
  const label = open ? 'Close sidebar' : 'Open sidebar'
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      title={label}
      aria-expanded={open}
      aria-controls="app-sidebar"
      className="flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-xl text-gray-400 hover:bg-white/8 hover:text-white focus-visible:outline-2 focus-visible:outline-[#C2B067]/60 transition-colors cursor-pointer"
    >
      <Icon size={18} />
    </button>
  )
}
