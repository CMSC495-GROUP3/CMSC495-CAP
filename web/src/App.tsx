import { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { TOKEN_KEY, isTokenExpired } from './api/client'
import LoginForm from './components/Auth/LoginForm'
import Sidebar from './components/Layout/Sidebar'
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
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
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
