import { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { TOKEN_KEY, isTokenExpired } from './api/client'
import LoginForm from './components/Auth/LoginForm'
import Sidebar from './components/Layout/Sidebar'
import SidebarToggle from './components/Layout/SidebarToggle'
import { useSidebar } from './hooks/useSidebar'
import { APP_NAME } from './config'
import ChatPage from './pages/ChatPage'
import DocumentLibraryPage from './pages/DocumentLibraryPage'

/** True when localStorage holds a JWT that has not yet expired. */
function readIsAuthenticated(): boolean {
  const token = localStorage.getItem(TOKEN_KEY)
  if (!token) return false
  if (isTokenExpired(token)) {
    localStorage.removeItem(TOKEN_KEY)
    return false
  }
  return true
}

function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const sidebar = useSidebar()
  // On desktop the toggle lives in the sidebar while it is open. Everywhere
  // else (sidebar hidden, or any phone) a slim bar above the page holds it,
  // so pages never have to leave room for a floating button.
  const showTopBar = !sidebar.open || !sidebar.isDesktop

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar
        open={sidebar.open}
        isDesktop={sidebar.isDesktop}
        onToggle={sidebar.toggle}
        onNavigate={sidebar.isDesktop ? undefined : sidebar.close}
      />
      {!sidebar.isDesktop && sidebar.open && (
        <div
          className="fixed inset-0 z-30 bg-black/60"
          onClick={sidebar.close}
          aria-hidden="true"
        />
      )}
      <main className="flex-1 flex flex-col overflow-hidden min-w-0">
        {showTopBar && (
          <div className="flex-shrink-0 flex items-center gap-2 h-11 px-2 border-b border-white/8">
            <SidebarToggle open={sidebar.open} onToggle={sidebar.toggle} />
            {!sidebar.isDesktop && (
              <span className="text-xs font-extrabold tracking-[0.3em] uppercase text-[#C2B067]/80 select-none">
                {APP_NAME}
              </span>
            )}
          </div>
        )}
        {children}
      </main>
    </div>
  )
}

export default function App() {
  // Single source of truth for auth state lives here.
  // LoginForm calls onSuccess() which updates this state directly,
  // so App re-renders immediately and switches to the protected layout.
  const [isAuthenticated, setIsAuthenticated] = useState(readIsAuthenticated)

  if (!isAuthenticated) {
    return <LoginForm onSuccess={() => setIsAuthenticated(true)} />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route
          path="/chat"
          element={
            <ProtectedLayout>
              <ChatPage />
            </ProtectedLayout>
          }
        />
        <Route
          path="/documents"
          element={
            <ProtectedLayout>
              <DocumentLibraryPage />
            </ProtectedLayout>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
