import { useState, useCallback } from 'react'
import FileUpload from './components/FileUpload'
import CustomerProfile from './components/CustomerProfile'
import DocumentUpload from './components/DocumentUpload'
import SummaryCards from './components/SummaryCards'
import ResultsTable from './components/ResultsTable'

// ── Minimal browser-side CSV parser (auto-detects , or ; delimiter) ───────
function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 2) return []
  const delim = (lines[0].split(';').length > lines[0].split(',').length) ? ';' : ','
  const headers = lines[0].split(delim).map(h => h.trim().replace(/^"|"$/g, ''))
  return lines.slice(1).filter(l => l.trim()).map(line => {
    const values = []
    let cur = '', inQuote = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"') { inQuote = !inQuote }
      else if (ch === delim && !inQuote) { values.push(cur.trim()); cur = '' }
      else { cur += ch }
    }
    values.push(cur.trim())
    return Object.fromEntries(headers.map((h, i) => [h, (values[i] || '').replace(/^"|"$/g, '')]))
  })
}

export default function App() {
  const [csvFile,   setCsvFile]   = useState(null)
  const [customer,  setCustomer]  = useState(null)
  const [docFiles,  setDocFiles]  = useState([])
  const [status,    setStatus]    = useState('idle')  // idle | processing | done | error
  const [results,   setResults]   = useState([])
  const [progress,  setProgress]  = useState({ current: 0, total: 0 })
  const [errorMsg,  setErrorMsg]  = useState('')

  // ── Parse CSV on select ────────────────────────────────────────────────
  const handleCsvSelect = useCallback((file) => {
    setCsvFile(file)
    setCustomer(null)
    setResults([])
    setStatus('idle')

    const reader = new FileReader()
    reader.onload = (e) => {
      const rows = parseCSV(e.target.result)
      if (rows.length > 0) setCustomer(rows[0])
    }
    reader.readAsText(file)
  }, [])

  const handleClearCustomer = useCallback(() => {
    setCsvFile(null)
    setCustomer(null)
    setResults([])
    setStatus('idle')
    setDocFiles([])
  }, [])

  // ── Run extraction ─────────────────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (!csvFile || docFiles.length === 0) return

    setStatus('processing')
    setResults([])
    setProgress({ current: 0, total: docFiles.length })
    setErrorMsg('')

    const formData = new FormData()
    formData.append('csv_file', csvFile)
    docFiles.forEach(f => formData.append('documents', f))

    try {
      const response = await fetch('/verify-ocr', { method: 'POST', body: formData })

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(err.detail || 'Server error')
      }

      const reader  = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer    = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = JSON.parse(line.slice(6))

          if (payload.done) { setStatus('done'); continue }

          setProgress({ current: payload.index, total: payload.total })
          // Attach human decision fields (null until agent clicks)
          setResults(prev => [...prev, { ...payload, human_decision: null, agent_notes: '' }])
        }
      }

      setStatus('done')
    } catch (err) {
      setErrorMsg(err.message)
      setStatus('error')
    }
  }, [csvFile, docFiles])

  // ── Human decision callbacks ───────────────────────────────────────────
  const handleDecisionChange = useCallback((idx, decision) => {
    setResults(prev => prev.map((r, i) => i === idx ? { ...r, human_decision: decision } : r))
  }, [])

  const handleNotesChange = useCallback((idx, notes) => {
    setResults(prev => prev.map((r, i) => i === idx ? { ...r, agent_notes: notes } : r))
  }, [])

  // ── Export decided results as CSV ──────────────────────────────────────
  const handleExport = useCallback(() => {
    const decided = results.filter(r => r.human_decision)
    if (decided.length === 0) return

    const headers = [
      'Document', 'Doc_Type', 'Customer_ID', 'Registered_Name', 'Registered_Address',
      'Human_Decision', 'Agent_Notes',
      'OCR_Customer_Name', 'OCR_Customer_Address', 'OCR_Issue_Date',
      'Is_PO_Box', 'Address_Transliterated',
    ]
    const rows = decided.map(r => [
      r.doc_filename,
      r.doc_type || 'standard',
      r.row?.Customer_ID || '',
      r.row?.Registered_Name || '',
      r.row?.Registered_Address || '',
      r.human_decision || '',
      `"${(r.agent_notes || '').replace(/"/g, '""')}"`,
      r.doc_type === 'lease_agreement' ? (r.ocr_tenant_name || '') : (r.ocr_customer_name || ''),
      r.ocr_customer_address || '',
      r.ocr_issue_date || '',
      r.is_po_box ? 'Yes' : 'No',
      r.address_transliterated ? 'Yes' : 'No',
    ])
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = 'kyc_results.csv'; a.click()
    URL.revokeObjectURL(url)
  }, [results])

  // ── Derived stats (based on human decisions) ───────────────────────────
  const counts = results.reduce(
    (acc, r) => {
      const key = r.human_decision || 'Undecided'
      acc[key] = (acc[key] || 0) + 1
      return acc
    },
    { Pass: 0, Failed: 0, 'Needs More Info': 0, Undecided: 0 }
  )

  const isProcessing = status === 'processing'
  const isDone       = status === 'done'
  const pct = progress.total > 0
    ? Math.round((progress.current / progress.total) * 100)
    : 0
  const canRun = !!customer && docFiles.length > 0 && !isProcessing

  return (
    <div className="app-shell">
      {/* ── Header ── */}
      <header className="app-header">
        <span className="app-header-icon">🔍</span>
        <div>
          <h1>KYC Proof of Address Matcher</h1>
          <p>Automated identity verification</p>
        </div>
        <span className="badge">PoC v3.0</span>
      </header>

      {/* ── Two-section input area ── */}
      <div className="two-section">
        <div className="card section-card">
          <div className="card-title">1 · Customer Data</div>
          {!customer ? (
            <FileUpload
              file={csvFile}
              onFileSelect={handleCsvSelect}
              disabled={isProcessing}
            />
          ) : (
            <CustomerProfile
              customer={customer}
              onClear={handleClearCustomer}
            />
          )}
        </div>

        <div className="card section-card">
          <div className="card-title">2 · Proof of Address Documents</div>
          <DocumentUpload
            files={docFiles}
            onChange={setDocFiles}
            disabled={isProcessing}
          />
        </div>
      </div>

      {/* ── Progress bar + Run button ── */}
      <div className="card">
        {isProcessing && (
          <div style={{ marginBottom: 16 }}>
            <div className="progress-bar-wrap">
              <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
            </div>
            <p className="progress-label">
              Extracting document {progress.current} of {progress.total} ({pct}%)…
            </p>
          </div>
        )}

        <button className="btn btn-primary" onClick={handleRun} disabled={!canRun}>
          {isProcessing
            ? <><span className="spinner" /> Extracting…</>
            : '▶ Run Extraction'}
        </button>

        {!customer && <p className="run-hint">Upload a customer CSV to begin</p>}
        {customer && docFiles.length === 0 && (
          <p className="run-hint">Upload at least one proof-of-address document to run</p>
        )}
      </div>

      {/* ── Error banner ── */}
      {status === 'error' && (
        <div className="card" style={{ borderColor: '#fecaca', background: '#fef2f2' }}>
          <strong style={{ color: '#dc2626' }}>⚠ Error: </strong>
          <span style={{ color: '#7f1d1d' }}>{errorMsg}</span>
        </div>
      )}

      {/* ── Summary ── */}
      {(isProcessing || isDone) && results.length > 0 && (
        <div className="card">
          <div className="card-title">3 · Results Summary</div>
          <SummaryCards counts={counts} total={progress.total || results.length} />
        </div>
      )}

      {/* ── Comparison cards ── */}
      {results.length > 0 && (
        <div className="card">
          <div className="card-title">4 · Review &amp; Decide</div>
          <ResultsTable
            results={results}
            onExport={handleExport}
            isLive={isProcessing}
            onDecisionChange={handleDecisionChange}
            onNotesChange={handleNotesChange}
          />
        </div>
      )}
    </div>
  )
}
