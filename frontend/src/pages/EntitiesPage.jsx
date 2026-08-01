import { Fragment, useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

function ReviewQueue() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [actingOn, setActingOn] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getReviewQueue()
      .then(d => setItems(d.entities || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  async function act(frisId, action) {
    setActingOn(frisId)
    try {
      await api.reviewEntity(frisId, action)
      setItems(prev => prev.filter(e => e.fris_id !== frisId))
    } catch {
      // leave the item in place so the user can retry
    } finally {
      setActingOn(null)
    }
  }

  if (loading) return null
  if (items.length === 0) return null

  return (
    <div className="console-panel" style={{ marginBottom: 16 }}>
      <div className="console-panel-header">
        <h2>Review queue<span className="count-badge">{items.length}</span></h2>
        <p className="subtitle">
          Entity merges the fusion engine wasn't fully confident about — confirm they're the
          same real-world entity, or reject to flag the merge as wrong.
        </p>
      </div>
      <table className="console-table">
        <thead>
          <tr><th>FRIS ID</th><th>Entity type</th><th>Name / Key field</th><th>Confidence</th><th></th></tr>
        </thead>
        <tbody>
          {items.map(e => (
            <tr key={e.fris_id}>
              <td className="entity-link">{e.fris_id?.slice(0, 8)}…</td>
              <td>{e.entity_type}</td>
              <td>{e.properties?.name || e.properties?.company_name || e.properties?.title || '—'}</td>
              <td>{e.confidence != null ? `${Math.round(e.confidence * 100)}%` : '—'}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button
                  className="btn-sm"
                  disabled={actingOn === e.fris_id}
                  onClick={() => act(e.fris_id, 'confirm')}
                >Confirm</button>{' '}
                <button
                  className="btn-sm"
                  disabled={actingOn === e.fris_id}
                  onClick={() => act(e.fris_id, 'reject')}
                >Reject</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ExplainPanel({ frisId }) {
  const [explanation, setExplanation] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.explainEntity(frisId)
      .then(setExplanation)
      .catch(() => setExplanation(null))
      .finally(() => setLoading(false))
  }, [frisId])

  if (loading) return <div className="helper-text" style={{ padding: '8px 16px' }}>Loading explanation…</div>
  if (!explanation) return <div className="helper-text" style={{ padding: '8px 16px' }}>Couldn't load an explanation for this entity.</div>

  return (
    <div style={{ padding: '12px 16px', fontSize: '0.9em' }}>
      <div style={{ marginBottom: 8 }}>
        <strong>Match confidence:</strong> {explanation.match_confidence != null ? `${Math.round(explanation.match_confidence * 100)}%` : '—'}
        {explanation.needs_review && <span className="badge badge-warning" style={{ marginLeft: 8 }}>Needs review</span>}
      </div>
      <div style={{ marginBottom: 8 }}>
        <strong>Contributing sources:</strong>{' '}
        {explanation.contributing_sources.map(s => s.connector_name).join(', ') || '—'}
      </div>
      <table className="console-table">
        <thead><tr><th>Field</th><th>Value</th><th>Winning source</th><th>Source trust</th><th>Recorded at</th></tr></thead>
        <tbody>
          {explanation.fields.map(f => (
            <tr key={f.field}>
              <td>{f.field}</td>
              <td>{String(f.value)}</td>
              <td>{f.contributing_source_name || '—'}</td>
              <td>{f.source_trust_score != null ? f.source_trust_score.toFixed(2) : '—'}</td>
              <td>{f.recorded_at ? new Date(f.recorded_at).toLocaleString() : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function EntitiesPage() {
  const [entities, setEntities] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterType, setFilterType] = useState('')
  const [expandedId, setExpandedId] = useState(null)

  useEffect(() => {
    setLoading(true)
    api.listEntities(filterType || undefined)
      .then(d => setEntities(d.entities))
      .catch(() => setEntities([]))
      .finally(() => setLoading(false))
  }, [filterType])

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Entities</h1>
          <p className="subtitle">Browse the fused, deduplicated entities currently in the graph.</p>
        </div>
        <div className="page-header-actions">
          <select className="form-select" style={{ width: 200 }} value={filterType} onChange={e => setFilterType(e.target.value)}>
            <option value="">All entity types</option>
            <option value="Organization">Organization</option>
            <option value="Person">Person</option>
            <option value="Transaction">Transaction</option>
            <option value="Product">Product</option>
          </select>
        </div>
      </div>

      <ReviewQueue />

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Fused entities<span className="count-badge">{entities.length}</span></h2>
        </div>

        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : entities.length === 0 ? (
          <div className="empty-state">
            <div className="icon">◉</div>
            <div className="title">No entities found</div>
            <div className="desc">Run a sync on a connector to populate the graph.</div>
          </div>
        ) : (
          <table className="console-table">
            <thead>
              <tr><th>FRIS ID</th><th>Entity type</th><th>Name / Key field</th><th>Sources</th><th></th></tr>
            </thead>
            <tbody>
              {entities.map(e => (
                <Fragment key={e.fris_id}>
                  <tr>
                    <td className="entity-link">{e.fris_id?.slice(0, 8)}…</td>
                    <td>{e.entity_type}</td>
                    <td>{e.name || e.company_name || e.title || '—'}</td>
                    <td>{(() => { try { return JSON.parse(e._sources || '[]').length } catch { return 1 } })()}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className="btn-sm"
                        onClick={() => setExpandedId(expandedId === e.fris_id ? null : e.fris_id)}
                      >
                        {expandedId === e.fris_id ? 'Hide' : 'Why?'}
                      </button>
                    </td>
                  </tr>
                  {expandedId === e.fris_id && (
                    <tr>
                      <td colSpan={5} style={{ background: 'var(--panel-alt-bg, rgba(127,127,127,0.06))' }}>
                        <ExplainPanel frisId={e.fris_id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
