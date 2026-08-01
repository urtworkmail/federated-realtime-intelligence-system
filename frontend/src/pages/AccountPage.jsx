import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'

const ROLES = ['viewer', 'member', 'admin', 'owner']

function TeamPanel({ account }) {
  const [members, setMembers] = useState([])
  const [invites, setInvites] = useState([])
  const [loading, setLoading] = useState(true)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [lastInviteLink, setLastInviteLink] = useState(null)
  const [error, setError] = useState(null)

  const canManage = account?.role === 'owner' || account?.role === 'admin'
  const isOwner = account?.role === 'owner'

  const load = useCallback(() => {
    setLoading(true)
    const calls = [api.listTeam()]
    if (canManage) calls.push(api.listInvites())
    Promise.all(calls)
      .then(([teamRes, invitesRes]) => {
        setMembers(teamRes.members)
        if (invitesRes) setInvites(invitesRes.invites)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage])

  useEffect(() => { load() }, [load])

  async function handleInvite(e) {
    e.preventDefault()
    if (!inviteEmail.trim()) return
    setError(null)
    try {
      const result = await api.inviteMember(inviteEmail.trim(), inviteRole)
      setLastInviteLink(`${window.location.origin}${result.accept_url_path}`)
      setInviteEmail('')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleRevokeInvite(id) {
    await api.revokeInvite(id)
    load()
  }

  async function handleRoleChange(accountId, role) {
    setError(null)
    try {
      await api.updateMemberRole(accountId, role)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleRemove(accountId) {
    if (!confirm('Remove this teammate from your organization? They will lose access immediately.')) return
    setError(null)
    try {
      await api.removeMember(accountId)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="console-panel" style={{ marginBottom: 16 }}>
      <div className="console-panel-header">
        <h2>Team<span className="count-badge">{members.length}</span></h2>
        <p className="subtitle">
          Everyone here shares your organization — same connectors, same fused graph, same tenant boundary.
        </p>
      </div>

      {canManage && (
        <div className="console-panel-body" style={{ borderBottom: '1px solid var(--console-border)' }}>
          <form onSubmit={handleInvite} className="form-row" style={{ marginBottom: 8 }}>
            <input
              className="form-input" type="email" placeholder="teammate@company.com"
              value={inviteEmail} onChange={e => setInviteEmail(e.target.value)}
            />
            <select className="form-select" style={{ width: 140 }} value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            <button className="btn btn-primary" type="submit">Invite</button>
          </form>
          {lastInviteLink && (
            <div className="console-panel" style={{ marginBottom: 8, background: 'var(--console-bg-alt)' }}>
              <div className="console-panel-body">
                <p className="badge badge-warning" style={{ marginBottom: 8 }}>
                  <span className="dot" />No email is sent yet — copy this link and share it directly
                </p>
                <pre className="log-metadata">{lastInviteLink}</pre>
              </div>
            </div>
          )}
          {error && <p className="error-text">{error}</p>}

          {invites.length > 0 && (
            <table className="console-table" style={{ marginTop: 8 }}>
              <thead><tr><th>Pending invite</th><th>Role</th><th>Expires</th><th></th></tr></thead>
              <tbody>
                {invites.map(i => (
                  <tr key={i.id}>
                    <td>{i.email}</td>
                    <td>{i.role}</td>
                    <td>{new Date(i.expires_at).toLocaleDateString()}</td>
                    <td><button className="btn btn-sm btn-danger" onClick={() => handleRevokeInvite(i.id)}>Revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {loading ? (
        <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
      ) : (
        <table className="console-table">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {members.map(m => (
              <tr key={m.id}>
                <td>{m.name || '—'}</td>
                <td>{m.email}</td>
                <td>
                  {isOwner && m.id !== account.id ? (
                    <select className="form-select" value={m.role} onChange={e => handleRoleChange(m.id, e.target.value)}>
                      {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : m.role}
                </td>
                <td>
                  <span className={`badge ${m.status === 'active' ? 'badge-success' : 'badge-neutral'}`}>
                    <span className="dot" />{m.status}
                  </span>
                </td>
                <td>
                  {isOwner && m.id !== account.id && m.status === 'active' && (
                    <button className="btn btn-sm btn-danger" onClick={() => handleRemove(m.id)}>Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function AccountPage({ account, onLogout }) {
  const [keys, setKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [newKeyName, setNewKeyName] = useState('')
  const [revealedKey, setRevealedKey] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.listApiKeys().then(d => setKeys(d.keys)).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  async function handleCreateKey(e) {
    e.preventDefault()
    if (!newKeyName.trim()) return
    setError(null)
    try {
      const created = await api.createApiKey(newKeyName)
      setRevealedKey(created.key)
      setNewKeyName('')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleRevoke(id) {
    if (!confirm('Revoke this API key? Any integration using it will stop working immediately.')) return
    await api.revokeApiKey(id)
    load()
  }

  return (
    <div>
      <div className="page-header-row">
        <div className="page-title-block">
          <h1>Account</h1>
          <p className="subtitle">Your FRIS account and programmatic API keys.</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-default" onClick={onLogout}>Sign out</button>
        </div>
      </div>

      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div className="console-panel-header"><h2>Profile</h2></div>
        <div className="console-panel-body">
          <p style={{ fontSize: 14, marginBottom: 4 }}><strong>{account?.name || 'Unnamed account'}</strong></p>
          <p className="helper-text">{account?.email} · role: {account?.role}</p>
        </div>
      </div>

      <TeamPanel account={account} />

      <div className="console-panel">
        <div className="console-panel-header">
          <h2>API keys<span className="count-badge">{keys.length}</span></h2>
        </div>
        <div className="console-panel-body">
          <form onSubmit={handleCreateKey} className="form-row" style={{ marginBottom: 16 }}>
            <input
              className="form-input" placeholder="Key name, e.g. 'CI pipeline'"
              value={newKeyName} onChange={e => setNewKeyName(e.target.value)}
            />
            <button className="btn btn-primary" type="submit">Create key</button>
          </form>

          {revealedKey && (
            <div className="console-panel" style={{ marginBottom: 16, background: 'var(--console-bg-alt)' }}>
              <div className="console-panel-body">
                <p className="badge badge-warning" style={{ marginBottom: 8 }}><span className="dot" />Copy this now — it won't be shown again</p>
                <pre className="log-metadata">{revealedKey}</pre>
              </div>
            </div>
          )}
          {error && <p className="error-text">{error}</p>}
        </div>

        {loading ? (
          <div className="console-panel-body"><p className="helper-text">Loading…</p></div>
        ) : keys.length === 0 ? (
          <div className="empty-state">
            <div className="icon">🔑</div>
            <div className="title">No API keys yet</div>
            <div className="desc">Create one to call the FRIS API programmatically.</div>
          </div>
        ) : (
          <table className="console-table">
            <thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="mono-cell">{k.key_prefix}…</td>
                  <td>{new Date(k.created_at).toLocaleDateString()}</td>
                  <td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : '—'}</td>
                  <td>
                    {k.revoked
                      ? <span className="badge badge-danger"><span className="dot" />Revoked</span>
                      : <span className="badge badge-success"><span className="dot" />Active</span>}
                  </td>
                  <td>
                    {!k.revoked && (
                      <button className="btn btn-sm btn-danger" onClick={() => handleRevoke(k.id)}>Revoke</button>
                    )}
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
