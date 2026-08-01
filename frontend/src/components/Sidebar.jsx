import { useState } from 'react'

const NAV_GROUPS = [
  {
    label: 'Intelligence',
    items: [
      { id: 'overview', label: 'Overview', icon: '▣' },
      { id: 'ask', label: 'Ask', icon: '💬' },
      { id: 'graph', label: 'Fused Graph', icon: '◈' },
      { id: 'graph-explorer', label: 'Graph Explorer', icon: '🕸' },
      { id: 'entities', label: 'Entities', icon: '◉' },
      { id: 'context', label: 'Context Definitions', icon: '◈' },
    ]
  },
  {
    label: 'Data Sources',
    items: [
      { id: 'connectors', label: 'Connectors', icon: '🔌' },
    ]
  },
  {
    label: 'Billing',
    items: [
      { id: 'billing', label: 'Usage & Costs', icon: '💳' },
    ]
  },
  {
    label: 'Settings',
    items: [
      { id: 'providers', label: 'LLM Providers', icon: '🤖' },
      { id: 'account', label: 'Account', icon: '👤' },
    ]
  },
  {
    label: 'Admin',
    items: [
      { id: 'logs', label: 'Logs', icon: '🗒' },
    ]
  },
  {
    label: 'Resources',
    items: [
      { id: 'docs', label: 'Docs', icon: '📖' },
    ]
  },
]

export default function Sidebar({ activePage, onNavigate }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className={`console-sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-toggle">
        <button onClick={() => setCollapsed(c => !c)} title={collapsed ? 'Expand' : 'Collapse'}>
          {collapsed ? '»' : '«'}
        </button>
      </div>

      {NAV_GROUPS.map(group => (
        <div key={group.label}>
          <div className="sidebar-group-label">{group.label}</div>
          {group.items.map(item => (
            <div
              key={item.id}
              className={`sidebar-item ${activePage === item.id ? 'active' : ''}`}
              onClick={() => onNavigate(item.id)}
              title={collapsed ? item.label : undefined}
            >
              <span className="ico">{item.icon}</span>
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
