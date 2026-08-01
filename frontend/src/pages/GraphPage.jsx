import { useState } from 'react'
import { api } from '../utils/api'

const EXAMPLE_QUERY = 'MATCH (e:Entity) RETURN e LIMIT 25'

export default function GraphPage() {
  const [query, setQuery] = useState(EXAMPLE_QUERY)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function runQuery() {
    setLoading(true)
    setError(null)
    try {
      const res = await api.queryGraph(query)
      setResults(res.results)
    } catch (e) {
      setError(e.message)
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Fused Graph</h1>
          <p className="subtitle">Run a direct Cypher query against the live fusion graph.</p>
        </div>
      </div>

      <div className="console-panel">
        <div className="console-panel-header"><h2>Query</h2></div>
        <div className="console-panel-body">
          <textarea
            className="form-textarea"
            style={{ minHeight: 100 }}
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <div style={{ marginTop: 12 }}>
            <button className="btn btn-primary" onClick={runQuery} disabled={loading}>
              {loading ? 'Running…' : 'Run query'}
            </button>
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      </div>

      {results && (
        <div className="console-panel">
          <div className="console-panel-header">
            <h2>Results<span className="count-badge">{results.length}</span></h2>
          </div>
          <div className="console-panel-body">
            <pre style={{ fontSize: 12, fontFamily: 'var(--mono)', overflowX: 'auto' }}>
              {JSON.stringify(results, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
