/**
 * Axios client configured for the FastAPI backend.
 *
 * The request interceptor runs before every API call and automatically
 * attaches the JWT from localStorage as a Bearer token header.
 * This way no individual API function has to think about auth.
 *
 * The response interceptor handles 401s globally — if the token expires
 * mid-session, the user gets sent back to the login page automatically.
 */
import axios from 'axios'
import { TOKEN_KEY } from '../config'

export { TOKEN_KEY }

const client = axios.create({
  // In dev, Vite proxies /api → localhost:8000 (see vite.config.ts).
  // In production, Nginx handles the same routing — so this baseURL works in both environments.
  baseURL: '/',
})

// Attach JWT to every request
client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On 401, clear the stored token and reload to the login page
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      window.location.href = '/'
    }
    return Promise.reject(error)
  }
)

export default client
