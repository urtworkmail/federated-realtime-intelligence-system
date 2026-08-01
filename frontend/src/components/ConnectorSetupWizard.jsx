import { useState } from 'react'
import { api } from '../utils/api'
import { getFieldDefs, shouldShowField } from '../utils/connectorFields'
import DataViewer from './DataViewer'

const STEPS = ['Configure', 'Test', 'Review Schema', 'Sync']

export default function ConnectorSetupWizard({ connectorType, onClose, onComplete }) {
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
  const [formValues, setFormValues] = useState({})
  const [connectorId, setConnectorId] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [schema, setSchema] = useState(null)
  const [fieldEdits, setFieldEdits] = useState({})
  const [entityTypeOverride, setEntityTypeOverride] = useState('')
  const [syncResult, setSyncResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [uploading, setUploading] = useState(false)

  const fieldDefs = getFieldDefs(connectorType.type)

  function setFieldValue(key, value) {
    setFormValues(prev => ({ ...prev, [key]: value }))
  }

  async function handleFileSelect(fieldKey, file) {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const result = await api.uploadFile(file)
      setFormValues(prev => ({
        ...prev,
        [fieldKey]: result.content_base64,
        file_name: result.filename,
        storage_object_id: result.object_id,
      }))
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  function buildConfigPayload() {
    const payload = {}
    fieldDefs.forEach(f => {
      if (!shouldShowField(f, formValues)) return
      let val = formValues[f.key] ?? f.default
      if (val === undefined || val === '') return
      if (f.type === 'textarea' && f.key === 'field_selectors') {
        try { val = JSON.parse(val) } catch { /* leave as string, backend will error clearly */ }
      }
      payload[f.key] = val
    })
    return payload
  }

  async function handleCreateAndTest() {
    setLoading(true)
    setError(null)
    try {
      const created = await api.createConnector({
        connector_type: connectorType.type,
        name: name || connectorType.display_name,
        config: buildConfigPayload()
      })
      setConnectorId(created.connector_id)

      const result = await api.testConnector(created.connector_id)
      setTestResult(result)
      if (result.success) {
        setStep(1)
      } else {
        setError(result.message)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleDetectSchema() {
    setLoading(true)
    setError(null)
    try {
      const result = await api.detectSchema(connectorId)
      setSchema(result.schema)
      setEntityTypeOverride(result.schema.suggested_entity_type)
      setStep(2)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function updateFieldEdit(rawName, key, value) {
    setFieldEdits(prev => ({
      ...prev,
      [rawName]: { ...prev[rawName], [key]: value }
    }))
  }

  async function handleConfirmSchema() {
    setLoading(true)
    setError(null)
    try {
      const field_updates = schema.fields.map(f => ({
        raw_name: f.raw_name,
        user_confirmed_name: fieldEdits[f.raw_name]?.user_confirmed_name || f.suggested_name,
        user_confirmed_type: fieldEdits[f.raw_name]?.user_confirmed_type || f.field_type,
        mapped_to_entity_property: fieldEdits[f.raw_name]?.mapped_to_entity_property || null
      }))
      await api.confirmSchema(connectorId, { field_updates, entity_type_override: entityTypeOverride })
      setStep(3)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleSync() {
    setLoading(true)
    setError(null)
    try {
      const result = await api.syncConnector(connectorId)
      setSyncResult(result)
      if (!result.success) setError(result.error)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-panel" style={{ width: 720 }}>
        <div className="modal-header">
          <div className="modal-title">
            {connectorType.icon} Connect {connectorType.display_name}
          </div>
          <div className="modal-close" onClick={onClose}>✕</div>
        </div>

        <div className="modal-body">
          <div className="step-indicator">
            {STEPS.map((s, i) => (
              <div key={s} className={`step-dot ${i === step ? 'active' : i < step ? 'complete' : ''}`} />
            ))}
          </div>
          <p className="helper-text" style={{ marginBottom: 20 }}>
            Step {step + 1} of {STEPS.length}: {STEPS[step]}
          </p>

          {/* ---------- Step 0: Configure ---------- */}
          {step === 0 && (
            <>
              <div className="form-group">
                <label className="form-label">
                  Connector name <span className="optional-tag">friendly name for this instance</span>
                </label>
                <input
                  className="form-input"
                  placeholder={`e.g. "${connectorType.display_name} — Production"`}
                  value={name}
                  onChange={e => setName(e.target.value)}
                />
              </div>

              {fieldDefs.filter(f => f.type !== 'hidden' && shouldShowField(f, formValues)).map(f => (
                <div key={f.key} className="form-group">
                  <label className="form-label">
                    {f.label}
                    {f.required && <span className="required">*</span>}
                  </label>
                  {f.type === 'select' ? (
                    <select
                      className="form-select"
                      value={formValues[f.key] ?? f.default ?? ''}
                      onChange={e => setFieldValue(f.key, e.target.value)}
                    >
                      {f.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    </select>
                  ) : f.type === 'textarea' || f.type === 'code' ? (
                    <textarea
                      className="form-textarea"
                      placeholder={f.placeholder}
                      value={formValues[f.key] ?? ''}
                      onChange={e => setFieldValue(f.key, e.target.value)}
                    />
                  ) : f.type === 'file' ? (
                    <>
                      <input
                        className="form-input"
                        type="file"
                        accept={f.accept}
                        disabled={uploading}
                        onChange={e => handleFileSelect(f.key, e.target.files[0])}
                      />
                      {uploading && <p className="helper-text">Uploading…</p>}
                      {formValues.file_name && !uploading && (
                        <p className="helper-text">Uploaded: {formValues.file_name}</p>
                      )}
                    </>
                  ) : (
                    <input
                      className="form-input"
                      type={f.type === 'password' ? 'password' : 'text'}
                      placeholder={f.placeholder}
                      value={formValues[f.key] ?? f.default ?? ''}
                      onChange={e => setFieldValue(f.key, e.target.value)}
                    />
                  )}
                </div>
              ))}

              {formValues.storage_object_id && <DataViewer objectId={formValues.storage_object_id} />}
            </>
          )}

          {/* ---------- Step 1: Test result ---------- */}
          {step === 1 && testResult && (
            <div className="console-panel">
              <div className="console-panel-body">
                <div className="badge badge-success" style={{ marginBottom: 12 }}>
                  <span className="dot" /> Connected
                </div>
                <p style={{ fontSize: 13 }}>{testResult.message}</p>
                <p className="helper-text">Latency: {testResult.latency_ms}ms</p>
              </div>
            </div>
          )}

          {/* ---------- Step 2: Schema review ---------- */}
          {step === 2 && schema && (
            <>
              <div className="form-group">
                <label className="form-label">Entity type</label>
                <input
                  className="form-input"
                  value={entityTypeOverride}
                  onChange={e => setEntityTypeOverride(e.target.value)}
                />
                <p className="helper-text">
                  FRIS auto-detected this as the entity type. Adjust if it's not quite right —
                  for example "Organization", "Person", "Shipment", "Transaction".
                </p>
              </div>

              <table className="schema-table">
                <thead>
                  <tr>
                    <th>Source field</th>
                    <th>FRIS field name</th>
                    <th>Type</th>
                    <th>Sample</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {schema.fields.map(f => (
                    <tr key={f.raw_name}>
                      <td>
                        {f.raw_name}
                        {f.is_identifier && <div className="field-flag" style={{ marginTop: 4 }}>ID</div>}
                        {f.is_entity_name && <div className="field-flag" style={{ marginTop: 4 }}>NAME</div>}
                      </td>
                      <td>
                        <input
                          defaultValue={f.suggested_name}
                          onChange={e => updateFieldEdit(f.raw_name, 'user_confirmed_name', e.target.value)}
                        />
                      </td>
                      <td>
                        <select
                          defaultValue={f.field_type}
                          onChange={e => updateFieldEdit(f.raw_name, 'user_confirmed_type', e.target.value)}
                        >
                          {['string', 'number', 'boolean', 'date', 'datetime', 'json', 'array'].map(t => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      </td>
                      <td style={{ color: 'var(--console-text-secondary)', fontSize: 11 }}>
                        {f.sample_values.slice(0, 2).join(', ')}
                      </td>
                      <td>
                        <div className="confidence-bar">
                          <div className="confidence-fill" style={{ width: `${f.confidence * 100}%` }} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="helper-text" style={{ marginTop: 12 }}>
                Edit any field name or type above. You can also add a custom mapping later from the connector detail page.
              </p>
            </>
          )}

          {/* ---------- Step 3: Sync ---------- */}
          {step === 3 && (
            <div>
              {!syncResult && (
                <p className="helper-text">
                  Ready to run the first sync. This fetches data from the source, normalizes it
                  using the schema you confirmed, and fuses it into the FRIS graph.
                </p>
              )}
              {syncResult?.success && (
                <div className="console-panel">
                  <div className="console-panel-body">
                    <div className="badge badge-success" style={{ marginBottom: 12 }}>
                      <span className="dot" /> Sync complete
                    </div>
                    <p style={{ fontSize: 13, marginBottom: 8 }}>
                      Fetched {syncResult.records_fetched} records.
                    </p>
                    <p style={{ fontSize: 13 }}>
                      Created {syncResult.fusion_result.entities_created} new entities, merged{' '}
                      {syncResult.fusion_result.entities_merged} into existing ones, resolved{' '}
                      {syncResult.fusion_result.conflicts_resolved} field conflicts.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {error && <p className="error-text">{error}</p>}
        </div>

        <div className="modal-footer">
          <button className="btn btn-default" onClick={onClose}>
            {syncResult?.success ? 'Close' : 'Cancel'}
          </button>

          {step === 0 && (
            <button className="btn btn-primary" disabled={loading} onClick={handleCreateAndTest}>
              {loading ? 'Testing…' : 'Test Connection'}
            </button>
          )}
          {step === 1 && (
            <button className="btn btn-primary" disabled={loading} onClick={handleDetectSchema}>
              {loading ? 'Detecting…' : 'Detect Schema'}
            </button>
          )}
          {step === 2 && (
            <button className="btn btn-primary" disabled={loading} onClick={handleConfirmSchema}>
              {loading ? 'Saving…' : 'Confirm & Continue'}
            </button>
          )}
          {step === 3 && !syncResult && (
            <button className="btn btn-primary" disabled={loading} onClick={handleSync}>
              {loading ? 'Syncing…' : 'Run First Sync'}
            </button>
          )}
          {step === 3 && syncResult?.success && (
            <button className="btn btn-primary" onClick={() => onComplete(connectorId)}>
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
