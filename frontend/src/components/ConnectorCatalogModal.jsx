import { useState, useEffect, Fragment } from 'react'
import { api } from '../utils/api'

export default function ConnectorCatalogModal({ onClose, onSelect }) {
  const [connectors, setConnectors] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [docsOpenFor, setDocsOpenFor] = useState(null)

  useEffect(() => {
    api.listAvailableConnectors()
      .then(data => setConnectors(data.connectors))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = connectors.filter(c =>
    !search ||
    c.display_name.toLowerCase().includes(search.toLowerCase()) ||
    c.description.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" style={{ width: 880, maxHeight: '80vh' }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">Add a Connector</div>
          <div className="modal-close" onClick={onClose}>✕</div>
        </div>
        <div className="modal-body" style={{ overflowY: 'auto' }}>
          <p className="helper-text" style={{ marginBottom: 16 }}>
            Pick a data source to connect. FRIS auto-detects the schema once connected —
            you confirm or adjust field mappings before anything syncs into the graph.
            Click "Docs" on any row for setup instructions before configuring it.
          </p>

          <input
            className="form-input"
            style={{ marginBottom: 16, width: '100%' }}
            type="text"
            placeholder="Search connectors…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />

          {loading && <p className="helper-text">Loading connector catalog…</p>}
          {error && <p className="error-text">{error}</p>}

          {!loading && !error && (
            <table className="console-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  <th>Connector</th>
                  <th>Description</th>
                  <th style={{ width: 80 }}>Docs</th>
                  <th style={{ width: 100 }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(c => (
                  <Fragment key={c.type}>
                    <tr>
                      <td><span style={{ fontSize: 18 }}>{c.icon}</span></td>
                      <td style={{ fontWeight: 500 }}>{c.display_name}</td>
                      <td className="helper-text">{c.description}</td>
                      <td>
                        <button
                          className="btn btn-sm btn-default"
                          onClick={() => setDocsOpenFor(docsOpenFor === c.type ? null : c.type)}
                        >
                          {docsOpenFor === c.type ? 'Hide' : 'Docs'}
                        </button>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn btn-sm btn-primary" onClick={() => onSelect(c)}>
                          Connect
                        </button>
                      </td>
                    </tr>
                    {docsOpenFor === c.type && c.docs && (
                      <tr>
                        <td colSpan={5}>
                          <pre className="log-metadata" style={{ whiteSpace: 'pre-wrap' }}>{c.docs}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}

          {!loading && !error && filtered.length === 0 && (
            <p className="helper-text">No connectors match "{search}".</p>
          )}
        </div>
      </div>
    </div>
  )
}
