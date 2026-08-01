export default function TopNav({ onSearchSelect, theme, onToggleTheme, account, onLogout }) {
  return (
    <div className="top-nav">
      <div className="top-nav-logo">
        <span className="mark">F</span>
        <span>FRIS Console</span>
      </div>

      <div className="top-nav-divider" />

      <div className="top-nav-search">
        <span className="search-icon">⌕</span>
        <input
          type="text"
          placeholder="Search connectors, entities, sync jobs…"
          onChange={(e) => onSearchSelect?.(e.target.value)}
        />
      </div>

      <div className="top-nav-right">
        <div className="top-nav-pill">
          <span className="env-badge">Local · Dev</span>
        </div>
        <button
          className="top-nav-pill theme-toggle"
          onClick={onToggleTheme}
          title={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
        >
          <span>{theme === 'light' ? '🌙' : '☀️'}</span>
        </button>
        <div className="top-nav-pill">
          <span>⚙</span>
        </div>
        <div className="top-nav-pill" onClick={onLogout} title="Sign out" style={{ cursor: onLogout ? 'pointer' : 'default' }}>
          <span>👤</span>
          <span>{account?.name || account?.email || 'Account'}</span>
        </div>
      </div>
    </div>
  )
}
