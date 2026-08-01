import { useState } from 'react'
import { api } from '../utils/api'

export default function AskPage() {
  const [query, setQuery] = useState('')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleAsk(e) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    const asked = query
    try {
      const result = await api.runQuery({ query: asked })
      setHistory(h => [{ query: asked, result }, ...h])
      setQuery('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Ask</h1>
          <p className="subtitle">
            Ask a plain-English question about your fused data. FRIS retrieves the most
            relevant entities and grounds its answer in them.
          </p>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-body">
          <form onSubmit={handleAsk} style={{ display: 'flex', gap: 12 }}>
            <input
              className="form-input" style={{ flex: 1 }}
              placeholder="e.g. Which suppliers are based in Berlin?"
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
            <button className="btn btn-primary" disabled={loading} type="submit">
              {loading ? 'Thinking…' : 'Ask'}
            </button>
          </form>
          {error && <p className="error-text" style={{ marginTop: 12 }}>{error}</p>}
        </div>
      </div>

      {history.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="icon">💬</div>
          <div className="title">Ask FRIS anything about your fused graph</div>
          <div className="desc">Answers are grounded in retrieved entities — sources are always shown.</div>
        </div>
      ) : (
        history.map((item, i) => (
          <div className="console-panel" key={i} style={{ marginBottom: 16 }}>
            <div className="console-panel-header">
              <h2>{item.query}</h2>
            </div>
            <div className="console-panel-body">
              {item.result.needs_clarification ? (
                <div className="badge badge-warning" style={{ marginBottom: 8 }}>
                  <span className="dot" /> Needs clarification
                </div>
              ) : (
                <div className="badge badge-success" style={{ marginBottom: 8 }}>
                  <span className="dot" /> {item.result.provider}
                </div>
              )}
              <p style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>
                {item.result.needs_clarification ? item.result.question : item.result.answer}
              </p>
              {item.result.sources?.length > 0 && (
                <>
                  <p className="helper-text" style={{ marginTop: 12, marginBottom: 6 }}>Sources</p>
                  <table className="console-table">
                    <thead><tr><th>Entity type</th><th>FRIS ID</th><th>Similarity</th></tr></thead>
                    <tbody>
                      {item.result.sources.map(s => (
                        <tr key={s.fris_id}>
                          <td>{s.entity_type}</td>
                          <td className="mono-cell">{s.fris_id}</td>
                          <td>{s.similarity_score?.toFixed(3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              {item.result.tokens && (
                <p className="helper-text" style={{ marginTop: 8 }}>
                  {item.result.tokens.total} tokens ({item.result.tokens.prompt} prompt + {item.result.tokens.completion} completion)
                </p>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
