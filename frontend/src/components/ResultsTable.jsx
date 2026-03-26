import { useState, useMemo } from 'react'

const FILTERS = ['All', 'Pass', 'Failed', 'Needs More Info', 'Undecided']
const DECISION_OPTIONS = ['Pass', 'Failed', 'Needs More Info']

export default function ResultsTable({ results, onExport, isLive, onDecisionChange, onNotesChange }) {
  const [filter, setFilter] = useState('All')

  const visible = useMemo(() => {
    if (filter === 'All') return results
    if (filter === 'Undecided') return results.filter(r => !r.human_decision)
    return results.filter(r => r.human_decision === filter)
  }, [results, filter])

  const chipClass = (f) => {
    if (f !== filter) return 'filter-chip'
    const map = {
      All: 'all', Pass: 'pass', Failed: 'fail',
      'Needs More Info': 'needs', Undecided: 'undecided'
    }
    return `filter-chip active-${map[f]}`
  }

  const decidedCount = results.filter(r => r.human_decision).length

  return (
    <>
      <div className="table-toolbar">
        <div className="filter-group">
          {FILTERS.map(f => (
            <button key={f} className={chipClass(f)} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>
          {isLive && '⚡ Live · '}{visible.length} result{visible.length !== 1 ? 's' : ''}
        </span>
        <button
          className="btn btn-secondary"
          onClick={onExport}
          disabled={decidedCount === 0}
          title={decidedCount === 0 ? 'Make at least one decision to export' : `Export ${decidedCount} decided result(s)`}
        >
          ↓ Export CSV {decidedCount > 0 ? `(${decidedCount})` : ''}
        </button>
      </div>

      {visible.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">🔎</div>
          <p>No results for this filter yet.</p>
        </div>
      )}

      <div className="comparison-cards">
        {visible.map((r, i) => {
          const isLease = r.doc_type === 'lease_agreement'
          const resultIdx = results.indexOf(r)
          const isNew = i === visible.length - 1 && isLive

          return (
            <div key={`${r.doc_filename}-${resultIdx}`} className={`comparison-card${isNew ? ' row-new' : ''}`}>

              {/* ── Card header ── */}
              <div className="cc-header">
                <span className="cc-filename">📄 {r.doc_filename}</span>
                <span className={`cc-type-badge ${isLease ? 'lease' : 'standard'}`}>
                  {isLease ? 'Lease Agreement' : 'Standard'}
                </span>
                {r.is_po_box && (
                  <span className="cc-flag-badge po-box">⚠️ PO Box</span>
                )}
                {r.address_transliterated && (
                  <span className="cc-flag-badge translated">🔤 Transliterated</span>
                )}
                {isLease && r.ocr_both_signed === false && (
                  <span className="cc-flag-badge unsigned">⚠️ Unsigned</span>
                )}
                {r.ocr_error && (
                  <span className="cc-flag-badge ocr-error">⚠️ OCR Error</span>
                )}
              </div>

              {/* ── Comparison grid ── */}
              <div className="cc-comparison">

                {/* Left: registered data (on file) */}
                <div className="cc-col cc-col-registered">
                  <div className="cc-col-header">On File (Registered)</div>
                  <div className="cc-field">
                    <div className="cc-field-label">Name</div>
                    <div className="cc-field-value">{r.row?.Registered_Name || '—'}</div>
                  </div>
                  <div className="cc-field">
                    <div className="cc-field-label">Address</div>
                    <div className="cc-field-value">{r.row?.Registered_Address || '—'}</div>
                  </div>
                </div>

                {/* Right: OCR extracted */}
                <div className="cc-col cc-col-extracted">
                  <div className="cc-col-header">On Document (OCR Extracted)</div>

                  {!isLease ? (
                    <>
                      <div className="cc-field">
                        <div className="cc-field-label">Customer Name</div>
                        <div className="cc-field-value">{r.ocr_customer_name || '—'}</div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Customer Address</div>
                        <div className="cc-field-value">
                          {r.ocr_customer_address || '—'}
                          {r.address_transliterated && r.address_original && (
                            <div className="cc-original-script">
                              Original: {r.address_original}
                            </div>
                          )}
                          {r.is_po_box && (
                            <div className="cc-po-warning">
                              ⚠️ PO Box — not an accepted residential address
                            </div>
                          )}
                        </div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Issue Date</div>
                        <div className="cc-field-value">{r.ocr_issue_date || '—'}</div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="cc-field">
                        <div className="cc-field-label">Tenant Name</div>
                        <div className="cc-field-value">{r.ocr_tenant_name || '—'}</div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Property Address</div>
                        <div className="cc-field-value">
                          {r.ocr_customer_address || '—'}
                          {r.address_transliterated && r.address_original && (
                            <div className="cc-original-script">
                              Original: {r.address_original}
                            </div>
                          )}
                        </div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Landlord Name</div>
                        <div className="cc-field-value">{r.ocr_landlord_name || '—'}</div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Both Signed</div>
                        <div className="cc-field-value">
                          {r.ocr_both_signed === true
                            ? '✅ Yes'
                            : r.ocr_both_signed === false
                            ? '❌ No — incomplete document'
                            : '—'}
                        </div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Lease Duration</div>
                        <div className="cc-field-value">{r.ocr_lease_duration || '—'}</div>
                      </div>
                      <div className="cc-field">
                        <div className="cc-field-label">Issue Date</div>
                        <div className="cc-field-value">{r.ocr_issue_date || '—'}</div>
                      </div>
                    </>
                  )}

                  {r.ocr_error && (
                    <div className="cc-ocr-error">OCR Error: {r.ocr_error}</div>
                  )}
                </div>
              </div>

              {/* ── Human decision section ── */}
              <div className="cc-decision-section">
                <span className="cc-decision-label">Agent Decision</span>
                <div className="cc-decision-buttons">
                  {DECISION_OPTIONS.map(d => {
                    const cls = d === 'Pass' ? 'pass'
                      : d === 'Failed' ? 'failed'
                      : 'needs-more-info'
                    const icon = d === 'Pass' ? '✅' : d === 'Failed' ? '❌' : '⚠️'
                    const isSelected = r.human_decision === d
                    return (
                      <button
                        key={d}
                        className={`cc-decision-btn ${cls}${isSelected ? ' selected' : ''}`}
                        onClick={() => onDecisionChange(resultIdx, isSelected ? null : d)}
                      >
                        {icon} {d}
                      </button>
                    )
                  })}
                </div>
                <input
                  type="text"
                  className="cc-notes-input"
                  placeholder="Optional notes…"
                  value={r.agent_notes || ''}
                  onChange={(e) => onNotesChange(resultIdx, e.target.value)}
                />
              </div>

            </div>
          )
        })}
      </div>
    </>
  )
}
