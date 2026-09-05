import { useEffect, useState } from 'react'
import { useMediaQuery } from './useMediaQuery'

/** Tailwind's `md` breakpoint. Below it the sidebar is a drawer over the page. */
const DESKTOP_QUERY = '(min-width: 768px)'
const STORAGE_KEY = 'sidebar_open'

function readDesktopPreference(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'closed'
  } catch {
    return true
  }
}

/**
 * Whether the sidebar is showing, and the controls to change that.
 *
 * On desktop the choice is remembered across reloads. On a phone the sidebar
 * always starts closed and closes again after navigation, because it covers
 * most of the screen; that state is never saved.
 */
export function useSidebar() {
  const isDesktop = useMediaQuery(DESKTOP_QUERY)
  const [open, setOpen] = useState(() => isDesktop && readDesktopPreference())

  // Crossing the breakpoint resets to that layout's default in the same
  // render, so a rotated phone never shows the desktop rail as a drawer.
  const [renderedDesktop, setRenderedDesktop] = useState(isDesktop)
  if (isDesktop !== renderedDesktop) {
    setRenderedDesktop(isDesktop)
    setOpen(isDesktop && readDesktopPreference())
  }

  useEffect(() => {
    if (!isDesktop) return
    try {
      localStorage.setItem(STORAGE_KEY, open ? 'open' : 'closed')
    } catch {
      // Private mode or blocked storage: the choice just does not persist.
    }
  }, [open, isDesktop])

  // Escape closes the drawer, the same as tapping the backdrop.
  useEffect(() => {
    if (isDesktop || !open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isDesktop, open])

  return {
    open,
    isDesktop,
    toggle: () => setOpen((prev) => !prev),
    close: () => setOpen(false),
  }
}
