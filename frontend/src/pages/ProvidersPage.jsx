import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

const PROVIDER_META = {
  fris: { label: 'FRIS Hosted', icon: '◈', authType: 'apikey', description: 'Offline default — no external calls, no key needed.' },
  openai: { label: 'OpenAI', icon: '🤖', authType: 'apikey', description: 'Bring your own OpenAI API key.' },
  anthropic: { label: 'Anthropic', icon: '✳', authType: 'apikey', description: 'Bring your own Anthropic API key.' },
  google: { label: 'Google Gemini', icon: '✦', authType: 'oauth', description: 'Connect via Google Cloud OAuth.' },
  azure: { label: 'Azure OpenAI', icon: '▤', authType: 'oauth', description: 'Connect via Microsoft Entra OAuth.' },
}

export default function ProvidersPage({ account }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [keyDrafts, setKeyDrafts] = useState({})
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.listProviders().then(setData).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const configuredByProvider = Object.fromEntries((data?.configured || []).map(c => [c.provider, c]))

  async function handleSaveKey(provider) {
    setError(null)
    try {
      await api.setApiKeyProvider(provider, keyDrafts[provider])
      setKeyDrafts(d => ({ ...d, [provider]: '' }))
      setToast(`${PROVIDER_META[provider].label} connected`)
      load()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSetDefault(provider) {
    await api.setDefaultProvider(provider)
    load()
  }

  async function handleDisconnect(provider) {
    await api.deleteProvider(provider)
    load()
  }

  function handleConnectOAuth(provider) {
    window.open(api.startOAuth(provider, account?.id), '_blank', 'width=520,height=640')
  }

  const allProviders = ['fris', 'openai', 'anthropic', 'google', 'azure']

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>LLM Providers</h1>
          <p className="subtitle">
            Bring your own AI provider. API-key providers connect instantly; Google and Azure
            use OAuth since that's what those platforms require.
          </p>
        </div>
      </div>

      {error && <p className="error-text" style={{ marginBottom: 12 }}>{error}</p>}
      {toast && <div className="toast" onAnimationEnd={() => setToast(null)}>{toast}</div>}

      {loading ? (
        <p className="helper-text">Loading…</p>
      ) : (
        allProviders.map(provider => {
          const meta = PROVIDER_META[provider]
          const configured = configuredByProvider[provider]
          const isDefault = data?.default_provider === provider
          return (
            <div className="console-panel" key={provider} style={{ marginBottom: 16 }}>
              <div className="console-panel-header">
                <h2>{meta.icon} {meta.label}
                  {isDefault && <span className="badge badge-info" style={{ marginLeft: 8 }}><span className="dot" />Default</span>}
                  {configured && <span className="badge badge-success" style={{ marginLeft: 8 }}><span className="dot" />Connected</span>}
                </h2>
              </div>
              <div className="console-panel-body">
                <p className="helper-text" style={{ marginBottom: 12 }}>{meta.description}</p>

                {meta.authType === 'apikey' && provider !== 'fris' && (
                  <div className="form-row">
                    <input
                      className="form-input" type="password"
                      placeholder={configured ? 'Key saved — enter a new one to replace it' : 'Paste API key'}
                      value={keyDrafts[provider] || ''}
                      onChange={e => setKeyDrafts(d => ({ ...d, [provider]: e.target.value }))}
                    />
                    <button
                      className="btn btn-default"
                      disabled={!keyDrafts[provider]}
                      onClick={() => handleSaveKey(provider)}
                    >
                      Save key
                    </button>
                  </div>
                )}

                {meta.authType === 'oauth' && (
                  <button className="btn btn-default" onClick={() => handleConnectOAuth(provider)}>
                    {configured ? 'Reconnect' : 'Connect'} {meta.label}
                  </button>
                )}

                <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                  {(configured || provider === 'fris') && !isDefault && (
                    <button className="btn btn-sm btn-default" onClick={() => handleSetDefault(provider)}>Make default</button>
                  )}
                  {configured && provider !== 'fris' && (
                    <button className="btn btn-sm btn-danger" onClick={() => handleDisconnect(provider)}>Disconnect</button>
                  )}
                </div>
              </div>
            </div>
          )
        })
      )}
    </div>
  )
}
