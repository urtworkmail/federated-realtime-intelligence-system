import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

function StatusBadge({ status }) {
  const map = {
    connected: { cls: 'badge-success', label: 'Connected' },
    failed: { cls: 'badge-danger', label: 'Failed' },
    pending: { cls: 'badge-neutral', label: 'Pending' },
    syncing: { cls: 'badge-warning', label: 'Syncing' },
    paused: { cls: 'badge-neutral', label: 'Paused' },
  }
  const cfg = map[status] || map.pending
  return <span className={`badge ${cfg.cls}`}><span className="dot" />{cfg.label}</span>
}

function SchemaDriftBadge({ drift }) {
  if (!drift) return null
  const newCount = drift.new_fields?.length || 0
  const missingCount = drift.missing_fields?.length || 0
  const parts = []
  if (newCount) parts.push(`${newCount} new field${newCount > 1 ? 's' : ''}`)
  if (missingCount) parts.push(`${missingCount} missing field${missingCount > 1 ? 's' : ''}`)
  return (
    <span
      className="badge badge-warning"
      title={`Source schema changed since last confirmation: ${parts.join(', ')}. Check Logs (schema_drift) for details.`}
      style={{ marginLeft: 6 }}
    >
      <span className="dot" />Schema drift
    </span>
  )
}

export default function ConnectorsPage({ onAddConnector }) {
  const [connectors, setConnectors] = useState([])
  const [loading, setLoading] = useState(true)
  const [syncingId, setSyncingId] = useState(null)
  const [toast, setToast] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.listConnectors().then(data => setConnectors(data.connectors)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  async function handleSync(id) {
    setSyncingId(id)
    try {
      const result = await api.syncConnector(id)
      if (result.success) {
        setToast({
          type: 'success',
          title: 'Sync complete',
          body: `${result.fusion_result.entities_created} created, ${result.fusion_result.entities_merged} merged`
        })
      } else {
        setToast({ type: 'error', title: 'Sync failed', body: result.error })
      }
      load()
    } finally {
      setSyncingId(null)
      setTimeout(() => setToast(null), 4000)
    }
  }

  async function handleDelete(id) {
    if (!confirm('Remove this connector? This does not delete already-fused entities.')) return
    await api.deleteConnector(id)
    load()
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Connectors</h1>
          <p className="subtitle">
            Data sources feeding the FRIS fusion engine. Each connector ingests, normalizes,
            and reconciles its data into the shared entity graph.
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-default" onClick={load}>↻ Refresh</button>
          <button className="btn btn-primary" onClick={onAddConnector}>+ Add Connector</button>
        </div>
      </div>

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Configured sources<span className="count-badge">{connectors.length}</span></h2>
        </div>

        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : connectors.length === 0 ? (
          <div className="empty-state">
            <div className="icon">🔌</div>
            <div className="title">No connectors yet</div>
            <div className="desc">Connect your first data source to start fusing intelligence.</div>
            <button className="btn btn-primary" onClick={onAddConnector}>+ Add Connector</button>
          </div>
        ) : (
          <table className="console-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Status</th>
                <th>Records ingested</th>
                <th>Last synced</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {connectors.map(c => (
                <tr key={c.connector_id}>
                  <td style={{ fontWeight: 500 }}>{c.name}</td>
                  <td>{c.connector_type}</td>
                  <td>
                    <StatusBadge status={syncingId === c.connector_id ? 'syncing' : c.status} />
                    <SchemaDriftBadge drift={c.schema_drift} />
                  </td>
                  <td>{c.total_records_ingested.toLocaleString()}</td>
                  <td>{c.last_synced ? new Date(c.last_synced).toLocaleString() : '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="btn btn-sm btn-default"
                      disabled={syncingId === c.connector_id}
                      onClick={() => handleSync(c.connector_id)}
                      style={{ marginRight: 6 }}
                    >
                      {syncingId === c.connector_id ? 'Syncing…' : 'Sync now'}
                    </button>
                    <button className="btn btn-sm btn-danger" onClick={() => handleDelete(c.connector_id)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {toast && (
        <div className={`toast ${toast.type === 'error' ? 'error' : ''}`}>
          <div className="toast-title">{toast.title}</div>
          <div className="toast-body">{toast.body}</div>
        </div>
      )}
    </div>
  )
}
