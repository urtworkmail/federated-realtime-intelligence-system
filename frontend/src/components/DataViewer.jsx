import { useState, useEffect } from 'react'
import { api } from '../utils/api'

// Lets a user see exactly what FRIS saw in an uploaded file — raw rows for
// CSV/Excel, extracted text + NLP-detected entities for PDF/email — before
// trusting the connector to fuse it into the graph.
export default function DataViewer({ objectId }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!objectId) return
    setLoading(true)
    setError(null)
    api.getUploadPreview(objectId)
      .then(setPreview)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [objectId])

  if (!objectId) return null
  if (loading) return <p className="helper-text">Loading preview…</p>
  if (error) return <p className="error-text">{error}</p>
  if (!preview) return null

  if (preview.type === 'table') {
    return (
      <div className="console-panel" style={{ marginTop: 12 }}>
        <div className="console-panel-header">
          <h2>Data preview<span className="count-badge">{preview.total_rows} rows</span></h2>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="console-table">
            <thead><tr>{preview.columns.map((c, i) => <th key={i}>{c || `col_${i}`}</th>)}</tr></thead>
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        {preview.total_rows > preview.rows.length && (
          <p className="helper-text" style={{ padding: '8px 16px' }}>
            Showing first {preview.rows.length} of {preview.total_rows} rows.
          </p>
        )}
      </div>
    )
  }

  if (preview.type === 'document') {
    return (
      <div className="console-panel" style={{ marginTop: 12 }}>
        <div className="console-panel-header">
          <h2>Data preview<span className="count-badge">{preview.entities.length} entities detected</span></h2>
        </div>
        <div className="console-panel-body">
          <p className="helper-text" style={{ marginBottom: 12 }}>
            NLP backend: <strong>{preview.nlp_backend}</strong> (offline, no external API call)
          </p>
          <table className="console-table" style={{ marginBottom: 16 }}>
            <thead><tr><th>Entity type</th><th>Name</th><th>Confidence</th><th>Context</th></tr></thead>
            <tbody>
              {preview.entities.map((e, i) => (
                <tr key={i}>
                  <td>{e.entity_type}</td>
                  <td>{e.name}</td>
                  <td>{Math.round(e.confidence * 100)}%</td>
                  <td style={{ fontSize: 11, color: 'var(--console-text-secondary)' }}>{e.source_snippet}</td>
                </tr>
              ))}
              {preview.entities.length === 0 && (
                <tr><td colSpan={4} className="helper-text">No entities detected in this document.</td></tr>
              )}
            </tbody>
          </table>
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 13 }}>Extracted text{preview.truncated ? ' (truncated)' : ''}</summary>
            <pre className="docs-code" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{preview.text_preview}</pre>
          </details>
        </div>
      </div>
    )
  }

  if (preview.type === 'text') {
    return (
      <div className="console-panel" style={{ marginTop: 12 }}>
        <div className="console-panel-header"><h2>Data preview</h2></div>
        <div className="console-panel-body">
          <pre className="docs-code" style={{ whiteSpace: 'pre-wrap' }}>{preview.text_preview}</pre>
          {preview.truncated && <p className="helper-text">Truncated — showing the first 5,000 characters.</p>}
        </div>
      </div>
    )
  }

  return <p className="helper-text">{preview.message}</p>
}
