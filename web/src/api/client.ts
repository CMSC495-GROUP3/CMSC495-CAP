/**
 * Axios client configured for the FastAPI backend.
 *
 * The request interceptor runs before every API call and automatically
 * attaches the JWT from localStorage as a Bearer token header.
 * This way no individual API function has to think about auth.
 *
 * The response interceptor handles 401s globally — if the token expires
 * mid-session, the user gets sent back to the login page automatically.
 * Login POSTs are excluded: a wrong password is also a 401, and the form
 * must show "Incorrect password." instead of being navigated away.
 */
import axios from 'axios'
import { TOKEN_KEY } from '../config'

export { TOKEN_KEY }

const LOGIN_PATH = '/api/auth/login'

const client = axios.create({
  // In dev, Vite proxies /api → localhost:8000 (see vite.config.ts).
  // In production, Nginx handles the same routing — so this baseURL works in both environments.
  baseURL: '/',
})

/** Clear the stored JWT and return to the sign-in page. */
export function signOut(): void {
  localStorage.removeItem(TOKEN_KEY)
  window.location.href = '/'
}

/**
 * True when `token` is expired, malformed, or missing an `exp` claim.
 */
export function isTokenExpired(token: string): boolean {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return true
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad = (4 - (b64.length % 4)) % 4
    const json = atob(b64 + '='.repeat(pad))
    const claims = JSON.parse(json) as { exp?: unknown }
    if (typeof claims.exp !== 'number') return true
    return claims.exp * 1000 <= Date.now()
  } catch {
    return true
  }
}

function isLoginRequest(url: string | undefined): boolean {
  if (!url) return false
  return url.includes(LOGIN_PATH)
}

// Attach JWT to every request
client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On 401, clear the stored token and reload to the login page —
// except for the login route itself (wrong password is also a 401).
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (
      error.response?.status === 401 &&
      !isLoginRequest(error.config?.url)
    ) {
      signOut()
    }
    return Promise.reject(error)
  }
)

export default client
