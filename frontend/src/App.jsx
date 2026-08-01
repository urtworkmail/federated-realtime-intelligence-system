import { useState, useEffect } from 'react'
import TopNav from './components/TopNav'
import Sidebar from './components/Sidebar'
import Breadcrumb from './components/Breadcrumb'
import ConnectorCatalogModal from './components/ConnectorCatalogModal'
import ConnectorSetupWizard from './components/ConnectorSetupWizard'
import LoginPage from './pages/LoginPage'
import OverviewPage from './pages/OverviewPage'
import AskPage from './pages/AskPage'
import ConnectorsPage from './pages/ConnectorsPage'
import EntitiesPage from './pages/EntitiesPage'
import ContextPage from './pages/ContextPage'
import GraphPage from './pages/GraphPage'
import GraphExplorerPage from './pages/GraphExplorerPage'
import BillingPage from './pages/BillingPage'
import ProvidersPage from './pages/ProvidersPage'
import AccountPage from './pages/AccountPage'
import LogsPage from './pages/LogsPage'
import DocsPage from './pages/DocsPage'
import { api, getToken, setToken } from './utils/api'

const PAGE_LABELS = {
  overview: 'Overview',
  ask: 'Ask',
  connectors: 'Connectors',
  entities: 'Entities',
  context: 'Context Definitions',
  graph: 'Fused Graph',
  'graph-explorer': 'Graph Explorer',
  billing: 'Usage & Costs',
  providers: 'LLM Providers',
  account: 'Account',
  logs: 'Logs',
  docs: 'Docs',
}

const VALID_PAGES = Object.keys(PAGE_LABELS)

function pageFromHash() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  return VALID_PAGES.includes(hash) ? hash : 'overview'
}

export default function App() {
  const [account, setAccount] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  // activePage is bound to the URL hash so a browser refresh keeps you on the
  // same page (and pages are directly linkable) rather than resetting to overview.
  const [activePage, setActivePage] = useState(pageFromHash)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [setupConnectorType, setSetupConnectorType] = useState(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('fris-theme') || 'light')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('fris-theme', theme)
  }, [theme])

  // Keep the URL hash and activePage in sync in both directions: navigate() updates
  // the hash, and browser back/forward (hashchange) updates activePage.
  useEffect(() => {
    const onHashChange = () => setActivePage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  function navigate(page) {
    if (window.location.hash.replace(/^#\/?/, '') !== page) {
      window.location.hash = `#/${page}`
    }
    setActivePage(page)
  }

  useEffect(() => {
    if (!getToken()) {
      setAuthChecked(true)
      return
    }
    api.me()
      .then(res => setAccount(res.account))
      .catch(() => setToken(null))
      .finally(() => setAuthChecked(true))
  }, [])

  function toggleTheme() {
    setTheme(t => (t === 'light' ? 'dark' : 'light'))
  }

  function handleSelectFromCatalog(connectorType) {
    setCatalogOpen(false)
    setSetupConnectorType(connectorType)
  }

  function handleWizardComplete() {
    setSetupConnectorType(null)
    navigate('connectors')
  }

  function handleLogout() {
    setToken(null)
    setAccount(null)
  }

  if (!authChecked) return null

  if (!account) {
    return <LoginPage onAuthenticated={setAccount} />
  }

  return (
    <div className="app-shell">
      <TopNav theme={theme} onToggleTheme={toggleTheme} account={account} onLogout={handleLogout} />

      <Breadcrumb
        trail={[
          { label: 'FRIS Console', onClick: () => navigate('overview') },
          { label: PAGE_LABELS[activePage] }
        ]}
      />

      <div className="console-body">
        <Sidebar activePage={activePage} onNavigate={navigate} />

        <div className="console-main">
          {activePage === 'overview' && <OverviewPage />}
          {activePage === 'ask' && <AskPage />}
          {activePage === 'connectors' && (
            <ConnectorsPage onAddConnector={() => setCatalogOpen(true)} />
          )}
          {activePage === 'entities' && <EntitiesPage />}
          {activePage === 'context' && <ContextPage />}
          {activePage === 'graph' && <GraphPage />}
          {activePage === 'graph-explorer' && <GraphExplorerPage />}
          {activePage === 'billing' && <BillingPage />}
          {activePage === 'providers' && <ProvidersPage account={account} />}
          {activePage === 'account' && <AccountPage account={account} onLogout={handleLogout} />}
          {activePage === 'logs' && <LogsPage />}
          {activePage === 'docs' && <DocsPage />}
        </div>
      </div>

      {catalogOpen && (
        <ConnectorCatalogModal
          onClose={() => setCatalogOpen(false)}
          onSelect={handleSelectFromCatalog}
        />
      )}

      {setupConnectorType && (
        <ConnectorSetupWizard
          connectorType={setupConnectorType}
          onClose={() => setSetupConnectorType(null)}
          onComplete={handleWizardComplete}
        />
      )}
    </div>
  )
}
