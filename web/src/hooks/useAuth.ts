import { TOKEN_KEY } from '../api/client'

export function useAuth() {
  function logout() {
    localStorage.removeItem(TOKEN_KEY)
    // Full reload so App re-reads localStorage and shows LoginForm
    window.location.href = '/'
  }

  return { logout }
}
