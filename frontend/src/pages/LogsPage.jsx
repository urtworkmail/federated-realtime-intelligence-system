import { useState, useEffect, useCallback, Fragment } from 'react'
import { api } from '../utils/api'

const CATEGORIES = [
  { id: '', label: 'All categories' },
  { id: 'server', label: 'Server', icon: '🖥' },
  { id: 'network', label: 'Network', icon: '🌐' },
  { id: 'console', label: 'Console', icon: '🖱' },
  { id: 'app', label: 'App', icon: '🧩' },
  { id: 'data_processing', label: 'Data Processing', icon: '⚙' },
]

const LEVELS = [
  { id: '', label: 'All levels' },
  { id: 'debug', label: 'Debug' },
  { id: 'info', label: 'Info' },
  { id: 'warning', label: 'Warning' },
  { id: 'error', label: 'Error' },
  { id: 'critical', label: 'Critical' },
]

const TIME_WINDOWS = [
  { id: '', label: 'All time' },
  { id: '1', label: 'Last hour' },
  { id: '24', label: 'Last 24 hours' },
  { id: '168', label: 'Last 7 days' },
]

function LevelBadge({ level }) {
  const map = {
    debug: 'badge-neutral',
    info: 'badge-info',
    warning: 'badge-warning',
    error: 'badge-danger',
    critical: 'badge-danger',
  }
  return <span className={`badge ${map[level] || 'badge-neutral'}`}><span className="dot" />{level}</span>
}

export default function LogsPage() {
  const [entries, setEntries] = useState([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState({ categories: {}, levels: {} })
  const [loading, setLoading] = useState(true)
  const [category, setCategory] = useState('')
  const [level, setLevel] = useState('')
  const [search, setSearch] = useState('')
  const [sinceHours, setSinceHours] = useState('')
  const [offset, setOffset] = useState(0)
  const [expandedId, setExpandedId] = useState(null)
  const limit = 50

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.getLogs({ category, level, search, sinceHours, limit, offset }),
      api.getLogCounts()
    ])
      .then(([logsRes, countsRes]) => {
        setEntries(logsRes.entries)
        setTotal(logsRes.total)
        setCounts(countsRes)
      })
      .finally(() => setLoading(false))
  }, [category, level, search, sinceHours, offset])

  useEffect(() => { load() }, [load])

  // Reset to first page whenever a filter changes
  useEffect(() => { setOffset(0) }, [category, level, search, sinceHours])

  async function handlePurge() {
    if (!confirm('Delete all logs older than 30 days? This cannot be undone.')) return
    await api.purgeLogs(30)
    load()
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Logs</h1>
          <p className="subtitle">
            Every server, network, console, app, and data-processing event — auto-logged
            and persisted. Filter below to narrow down what you're looking for.
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-default" onClick={load}>↻ Refresh</button>
          <button className="btn btn-default" onClick={handlePurge}>Purge older than 30 days</button>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-body" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="form-select" value={category} onChange={e => setCategory(e.target.value)}>
            {CATEGORIES.map(c => (
              <option key={c.id} value={c.id}>
                {c.label}{c.id && counts.categories[c.id] ? ` (${counts.categories[c.id]})` : ''}
              </option>
            ))}
          </select>

          <select className="form-select" value={level} onChange={e => setLevel(e.target.value)}>
            {LEVELS.map(l => (
              <option key={l.id} value={l.id}>
                {l.label}{l.id && counts.levels[l.id] ? ` (${counts.levels[l.id]})` : ''}
              </option>
            ))}
          </select>

          <select className="form-select" value={sinceHours} onChange={e => setSinceHours(e.target.value)}>
            {TIME_WINDOWS.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>

          <input
            className="form-input"
            style={{ flex: 1, minWidth: 220 }}
            type="text"
            placeholder="Search message or source…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Log entries<span className="count-badge">{total.toLocaleString()}</span></h2>
        </div>

        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : entries.length === 0 ? (
          <div className="empty-state">
            <div className="icon">🗒</div>
            <div className="title">No log entries match these filters</div>
            <div className="desc">Try widening the time range or clearing a filter.</div>
          </div>
        ) : (
          <>
            <table className="console-table">
              <thead>
                <tr>
                  <th style={{ width: 170 }}>Time</th>
                  <th style={{ width: 130 }}>Category</th>
                  <th style={{ width: 90 }}>Level</th>
                  <th>Message</th>
                  <th style={{ width: 160 }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(entry => (
                  <Fragment key={entry.id}>
                    <tr
                      onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                      style={{ cursor: entry.metadata ? 'pointer' : 'default' }}
                    >
                      <td className="mono-cell">{new Date(entry.occurred_at).toLocaleString()}</td>
                      <td>{entry.category}</td>
                      <td><LevelBadge level={entry.level} /></td>
                      <td>{entry.message}</td>
                      <td className="mono-cell">{entry.source || '—'}</td>
                    </tr>
                    {expandedId === entry.id && entry.metadata && (
                      <tr>
                        <td colSpan={5}>
                          <pre className="log-metadata">{JSON.stringify(entry.metadata, null, 2)}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>

            <div className="table-pagination">
              <button
                className="btn btn-sm btn-default"
                disabled={offset === 0}
                onClick={() => setOffset(o => Math.max(0, o - limit))}
              >
                ← Previous
              </button>
              <span className="helper-text">
                {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
              </span>
              <button
                className="btn btn-sm btn-default"
                disabled={offset + limit >= total}
                onClick={() => setOffset(o => o + limit)}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
