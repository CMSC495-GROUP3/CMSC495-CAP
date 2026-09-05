import { signOut } from '../api/client'

export function useAuth() {
  function logout() {
    signOut()
  }

  return { logout }
}
