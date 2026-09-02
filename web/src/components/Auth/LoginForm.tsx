import { useState } from 'react'
import client, { TOKEN_KEY } from '../../api/client'
import { APP_NAME } from '../../config'

interface Props {
  onSuccess: () => void
}

export default function LoginForm({ onSuccess }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await client.post('/api/auth/login', { password })
      localStorage.setItem(TOKEN_KEY, res.data.access_token)
      onSuccess()
    } catch {
      setError('Incorrect password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center h-screen w-full">
      <div className="w-full max-w-sm px-8 py-10 rounded-2xl border border-white/10 bg-white/3">
        <h1 className="text-xl font-semibold text-gray-100 mb-1">{APP_NAME}</h1>
        <p className="text-sm text-gray-400 mb-6">Enter the password to continue.</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoFocus
            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-[#C2B067]/40 transition-colors"
          />
          {error && <p className="text-xs text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={loading || !password}
            className="w-full py-2.5 rounded-xl bg-gradient-to-r from-[#A08340] to-[#C2B067] hover:from-[#B09450] hover:to-[#D4C278] disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium text-[#0e0e0e] transition-all cursor-pointer"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
