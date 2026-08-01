import { useState } from 'react'
import { api, setToken } from '../utils/api'

const inviteToken = new URLSearchParams(window.location.search).get('token')

export default function LoginPage({ onAuthenticated }) {
  const [mode, setMode] = useState(inviteToken ? 'accept-invite' : 'login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      let result
      if (mode === 'accept-invite') {
        result = await api.acceptInvite({ token: inviteToken, password, name: name || undefined })
      } else if (mode === 'login') {
        result = await api.login({ email, password })
      } else {
        result = await api.signup({ email, password, name: name || undefined })
      }
      setToken(result.token)
      window.history.replaceState({}, '', window.location.pathname)
      onAuthenticated(result.account)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--console-bg)'
    }}>
      <div className="modal-panel" style={{ width: 400 }}>
        <div className="modal-header">
          <div className="modal-title">
            <span className="mark" style={{
              display: 'inline-flex', width: 24, height: 24, borderRadius: 6,
              background: 'var(--accent)', color: '#fff', alignItems: 'center',
              justifyContent: 'center', marginRight: 8, fontFamily: 'var(--serif)'
            }}>F</span>
            FRIS Console
          </div>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="subtitle" style={{ marginBottom: 20 }}>
              {mode === 'login' ? 'Sign in to your account'
                : mode === 'accept-invite' ? "You've been invited to join a FRIS organization — set a password to accept"
                : 'Create a new FRIS account'}
            </p>

            {(mode === 'signup' || mode === 'accept-invite') && (
              <div className="form-group">
                <label className="form-label">Name <span className="optional-tag">optional</span></label>
                <input className="form-input" value={name} onChange={e => setName(e.target.value)} />
              </div>
            )}
            {mode !== 'accept-invite' && (
              <div className="form-group">
                <label className="form-label">Email<span className="required">*</span></label>
                <input
                  className="form-input" type="email" required
                  value={email} onChange={e => setEmail(e.target.value)}
                />
              </div>
            )}
            <div className="form-group">
              <label className="form-label">Password<span className="required">*</span></label>
              <input
                className="form-input" type="password" required minLength={8}
                value={password} onChange={e => setPassword(e.target.value)}
              />
            </div>
            {error && <p className="error-text">{error}</p>}
          </div>
          <div className="modal-footer" style={{ justifyContent: 'space-between' }}>
            {mode === 'accept-invite' ? (
              <span className="helper-text">Invite link</span>
            ) : (
              <button
                type="button" className="btn btn-link"
                onClick={() => setMode(m => (m === 'login' ? 'signup' : 'login'))}
              >
                {mode === 'login' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
              </button>
            )}
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? 'Please wait…' : mode === 'accept-invite' ? 'Accept & join' : mode === 'login' ? 'Sign in' : 'Create account'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
