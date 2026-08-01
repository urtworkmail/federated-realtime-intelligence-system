import { useState, useEffect, useCallback, Fragment } from 'react'
import { api } from '../utils/api'

function fmtUsd(n) {
  return `$${Number(n || 0).toFixed(4)}`
}

export default function BillingPage() {
  const [summary, setSummary] = useState(null)
  const [usage, setUsage] = useState({ entries: [], total: 0 })
  const [invoices, setInvoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState(null)
  const [offset, setOffset] = useState(0)
  const limit = 50

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.getBillingSummary(30),
      api.getUsage({ limit, offset }),
      api.getInvoices(6)
    ])
      .then(([s, u, inv]) => {
        setSummary(s)
        setUsage(u)
        setInvoices(inv.invoices)
      })
      .finally(() => setLoading(false))
  }, [offset])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Usage & Costs</h1>
          <p className="subtitle">
            Postpaid, metered usage across every action — LLM queries, searches, syncs,
            embeddings, and storage. No hard limits; this is a running ledger.
          </p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-default" onClick={load}>↻ Refresh</button>
        </div>
      </div>

      <div className="stat-tiles">
        <div className="stat-tile">
          <div className="label">Cost — last 30 days</div>
          <div className="value accent">{fmtUsd(summary?.total_cost_usd)}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Total tokens</div>
          <div className="value">{(summary?.total_tokens || 0).toLocaleString()}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Metered events</div>
          <div className="value">{(summary?.total_events || 0).toLocaleString()}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Event types active</div>
          <div className="value">{summary?.by_event_type?.length ?? 0}</div>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-header"><h2>Cost by event type (30d)</h2></div>
        {summary?.by_event_type?.length > 0 ? (
          <table className="console-table">
            <thead><tr><th>Event type</th><th>Events</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody>
              {summary.by_event_type.map(row => (
                <tr key={row.event_type}>
                  <td>{row.event_type}</td>
                  <td>{row.event_count}</td>
                  <td>{Math.round(row.total_tokens).toLocaleString()}</td>
                  <td>{fmtUsd(row.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            <div className="icon">💳</div>
            <div className="title">No usage recorded yet</div>
            <div className="desc">Run a sync, a search, or ask a question — usage shows up here.</div>
          </div>
        )}
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-header"><h2>Monthly invoices</h2></div>
        {invoices.length > 0 ? (
          <table className="console-table">
            <thead><tr><th>Period</th><th>Events</th><th>Tokens</th><th>Total</th></tr></thead>
            <tbody>
              {invoices.map(inv => (
                <tr key={inv.period}>
                  <td>{new Date(inv.period).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })}</td>
                  <td>{inv.event_count}</td>
                  <td>{Math.round(inv.total_tokens).toLocaleString()}</td>
                  <td>{fmtUsd(inv.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="console-panel-body"><p className="helper-text">No invoices yet.</p></div>
        )}
      </div>

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Usage ledger<span className="count-badge">{usage.total.toLocaleString()}</span></h2>
        </div>
        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : usage.entries.length === 0 ? (
          <div className="console-panel-body"><p className="helper-text">No entries.</p></div>
        ) : (
          <>
            <table className="console-table">
              <thead>
                <tr>
                  <th style={{ width: 170 }}>Time</th>
                  <th>Event type</th>
                  <th>Quantity</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.entries.map(entry => (
                  <Fragment key={entry.id}>
                    <tr
                      onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                      style={{ cursor: entry.metadata ? 'pointer' : 'default' }}
                    >
                      <td className="mono-cell">{new Date(entry.occurred_at).toLocaleString()}</td>
                      <td>{entry.event_type}</td>
                      <td>{entry.quantity} {entry.unit}</td>
                      <td>{Math.round(entry.tokens)}</td>
                      <td>{fmtUsd(entry.cost_usd)}</td>
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
              <button className="btn btn-sm btn-default" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - limit))}>← Previous</button>
              <span className="helper-text">{offset + 1}–{Math.min(offset + limit, usage.total)} of {usage.total.toLocaleString()}</span>
              <button className="btn btn-sm btn-default" disabled={offset + limit >= usage.total} onClick={() => setOffset(o => o + limit)}>Next →</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
