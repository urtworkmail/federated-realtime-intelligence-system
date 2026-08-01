import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

const ENTITY_TYPES = ['Organization', 'Person', 'Transaction', 'Product']

export default function ContextPage() {
  const [definitions, setDefinitions] = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState({ entity_type: 'Organization', property_name: '', canonical_field: '', description: '' })
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.listContext().then(d => setDefinitions(d.definitions || [])).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  async function handleSave(e) {
    e.preventDefault()
    if (!form.property_name || !form.canonical_field) return
    setSaving(true)
    try {
      await api.upsertContext(form.entity_type, form.property_name, {
        canonical_field: form.canonical_field,
        description: form.description || null
      })
      setForm({ ...form, property_name: '', canonical_field: '', description: '' })
      load()
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(entityType, propertyName) {
    await api.deleteContext(entityType, propertyName)
    load()
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Context Definitions</h1>
          <p className="subtitle">
            Canonical business definitions per entity type — tell FRIS (and every agent querying it)
            which fused field is authoritative when several similarly-named fields exist across sources.
          </p>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-header"><h2>Add / update a definition</h2></div>
        <div className="console-panel-body">
          <form onSubmit={handleSave} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div>
              <label className="form-label">Entity type</label>
              <select
                className="form-select"
                value={form.entity_type}
                onChange={e => setForm({ ...form, entity_type: e.target.value })}
              >
                {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">Property (e.g. "revenue")</label>
              <input
                className="form-input"
                value={form.property_name}
                onChange={e => setForm({ ...form, property_name: e.target.value })}
              />
            </div>
            <div>
              <label className="form-label">Canonical field (e.g. "annual_revenue_usd")</label>
              <input
                className="form-input"
                value={form.canonical_field}
                onChange={e => setForm({ ...form, canonical_field: e.target.value })}
              />
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <label className="form-label">Notes (optional)</label>
              <input
                className="form-input"
                value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </form>
        </div>
      </div>

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Definitions<span className="count-badge">{definitions.length}</span></h2>
        </div>
        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : definitions.length === 0 ? (
          <div className="empty-state">
            <div className="icon">◈</div>
            <div className="title">No definitions yet</div>
            <div className="desc">Add one above so the Ask assistant knows which field is authoritative per property.</div>
          </div>
        ) : (
          <table className="console-table">
            <thead>
              <tr><th>Entity type</th><th>Property</th><th>Canonical field</th><th>Notes</th><th></th></tr>
            </thead>
            <tbody>
              {definitions.map(d => (
                <tr key={`${d.entity_type}.${d.property_name}`}>
                  <td>{d.entity_type}</td>
                  <td>{d.property_name}</td>
                  <td><code>{d.canonical_field}</code></td>
                  <td>{d.description || '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="btn-sm" onClick={() => handleDelete(d.entity_type, d.property_name)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
