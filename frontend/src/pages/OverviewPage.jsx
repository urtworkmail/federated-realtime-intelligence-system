import { useState, useEffect } from 'react'
import { api } from '../utils/api'

export default function OverviewPage() {
  const [stats, setStats] = useState(null)
  const [connectors, setConnectors] = useState([])

  useEffect(() => {
    api.getGraphStats().then(setStats).catch(() => setStats({ total_entities: 0, total_relationships: 0, entities_by_type: [] }))
    api.listConnectors().then(d => setConnectors(d.connectors)).catch(() => setConnectors([]))
  }, [])

  const connected = connectors.filter(c => c.status === 'connected').length
  const failed = connectors.filter(c => c.status === 'failed').length

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Overview</h1>
          <p className="subtitle">Current state of the fusion engine across all connected sources.</p>
        </div>
      </div>

      <div className="stat-tiles">
        <div className="stat-tile">
          <div className="label">Total entities</div>
          <div className="value accent">{stats?.total_entities ?? '—'}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Relationships</div>
          <div className="value">{stats?.total_relationships ?? '—'}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Connectors active</div>
          <div className="value">{connected} / {connectors.length}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Connectors failing</div>
          <div className="value" style={{ color: failed > 0 ? 'var(--danger)' : 'var(--console-text)' }}>{failed}</div>
        </div>
      </div>

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Entities by type</h2>
        </div>
        {stats?.entities_by_type?.length > 0 ? (
          <table className="console-table">
            <thead><tr><th>Entity type</th><th>Count</th></tr></thead>
            <tbody>
              {stats.entities_by_type.map(row => (
                <tr key={row.type}>
                  <td>{row.type}</td>
                  <td>{row.count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            <div className="icon">◈</div>
            <div className="title">No entities yet</div>
            <div className="desc">Connect a data source and run a sync to populate the graph.</div>
          </div>
        )}
      </div>
    </div>
  )
}
