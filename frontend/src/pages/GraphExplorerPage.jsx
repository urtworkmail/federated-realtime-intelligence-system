import { useState, useEffect, useCallback, useRef } from 'react'
import cytoscape from 'cytoscape'
import { api } from '../utils/api'

function GraphCanvas({ subgraph, onNodeClick }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current) return
    const elements = [
      ...subgraph.nodes.map(n => ({
        data: { id: n.fris_id, label: n.properties?.name || n.properties?.full_name || n.entity_type, type: n.entity_type }
      })),
      ...subgraph.edges.map(e => ({
        data: { id: `${e.from}-${e.type}-${e.to}`, source: e.from, target: e.to, label: e.type }
      }))
    ]

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': '#6366F1',
            'label': 'data(label)',
            'font-size': 10,
            'color': 'var(--console-text, #1a1a1a)',
            'text-valign': 'bottom',
            'text-margin-y': 6,
            'width': 28,
            'height': 28,
          }
        },
        {
          selector: `node[id = "${subgraph.center}"]`,
          style: { 'background-color': '#0EA5E9', 'width': 36, 'height': 36 }
        },
        {
          selector: 'edge',
          style: {
            'width': 1.5,
            'line-color': '#9CA3AF',
            'target-arrow-color': '#9CA3AF',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'label': 'data(label)',
            'font-size': 8,
            'color': '#6B7280',
          }
        }
      ],
      layout: { name: 'cose', animate: false, padding: 30 }
    })

    cy.on('tap', 'node', evt => onNodeClick(evt.target.id()))
    cyRef.current = cy
    return () => cy.destroy()
  }, [subgraph])

  return <div ref={containerRef} style={{ width: '100%', height: 420, background: 'var(--console-bg-alt)', borderRadius: 'var(--radius)' }} />
}

export default function GraphExplorerPage() {
  const [entityType, setEntityType] = useState('')
  const [text, setText] = useState('')
  const [nodes, setNodes] = useState({ nodes: [], total: 0 })
  const [selected, setSelected] = useState(null)
  const [subgraph, setSubgraph] = useState(null)
  const [depth, setDepth] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const loadNodes = useCallback(() => {
    setLoading(true)
    api.browseNodes({ entityType: entityType || undefined, text: text || undefined, limit: 50 })
      .then(setNodes)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [entityType, text])

  useEffect(() => { loadNodes() }, [loadNodes])

  async function handleSelectNode(frisId) {
    setSelected(frisId)
    setError(null)
    try {
      const sg = await api.getSubgraph(frisId, depth)
      setSubgraph(sg)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Graph Explorer</h1>
          <p className="subtitle">Browse fused entities directly, or click one to visualize its relationships.</p>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-body" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            className="form-input" style={{ maxWidth: 200 }}
            placeholder="Entity type…" value={entityType}
            onChange={e => setEntityType(e.target.value)}
          />
          <input
            className="form-input" style={{ flex: 1, minWidth: 220 }}
            placeholder="Search property values…" value={text}
            onChange={e => setText(e.target.value)}
          />
          {selected && (
            <select className="form-select" value={depth} onChange={e => { setDepth(Number(e.target.value)); handleSelectNode(selected) }}>
              <option value={1}>Depth 1</option>
              <option value={2}>Depth 2</option>
              <option value={3}>Depth 3</option>
            </select>
          )}
        </div>
      </div>

      {error && <p className="error-text" style={{ marginBottom: 12 }}>{error}</p>}

      {selected && subgraph && (
        <div className="console-panel" style={{ marginBottom: 16 }}>
          <div className="console-panel-header">
            <h2>Relationship graph<span className="count-badge">{subgraph.nodes.length} nodes · {subgraph.edges.length} edges</span></h2>
          </div>
          <div className="console-panel-body">
            <GraphCanvas subgraph={subgraph} onNodeClick={handleSelectNode} />
          </div>
        </div>
      )}

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>Nodes<span className="count-badge">{nodes.total.toLocaleString()}</span></h2>
        </div>
        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : nodes.nodes.length === 0 ? (
          <div className="empty-state">
            <div className="icon">◈</div>
            <div className="title">No matching nodes</div>
            <div className="desc">Try clearing filters, or sync a connector to populate the graph.</div>
          </div>
        ) : (
          <table className="console-table">
            <thead><tr><th>FRIS ID</th><th>Type</th><th>Key properties</th></tr></thead>
            <tbody>
              {nodes.nodes.map(n => (
                <tr
                  key={n.fris_id}
                  onClick={() => handleSelectNode(n.fris_id)}
                  style={{ cursor: 'pointer', background: selected === n.fris_id ? 'var(--console-bg-alt)' : undefined }}
                >
                  <td className="mono-cell">{n.fris_id}</td>
                  <td>{n.entity_type}</td>
                  <td>
                    {Object.entries(n.properties || {})
                      .filter(([k]) => !k.startsWith('_'))
                      .slice(0, 3)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join('  ·  ')}
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
