import { useState, useMemo } from 'react'
import { DECISION_ICON } from '../App'

const FILTERS = ['All', 'Pass', 'Fail', 'Escalate']

export default function ResultsTable({ results, onExport, isLive }) {
  const [filter, setFilter] = useState('All')

  const visible = useMemo(
    () => filter === 'All' ? results : results.filter(r => r.decision === filter),
    [results, filter]
  )

  const chipClass = (f) => {
    if (f !== filter) return 'filter-chip'
    const map = { All: 'all', Pass: 'pass', Fail: 'fail', Escalate: 'escalate' }
    return `filter-chip active-${map[f]}`
  }

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
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted)' }}>
          {isLive && '⚡ Live · '}{visible.length} row{visible.length !== 1 ? 's' : ''}
        </span>
        <button className="btn btn-secondary" onClick={onExport} disabled={results.length === 0}>
          ↓ Export CSV
        </button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Document</th>
              <th>Decision</th>
              <th>Reasoning</th>
              <th>OCR Name</th>
              <th>OCR Address</th>
              <th>Issue Date</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <div className="empty-state">
                    <div className="empty-state-icon">🔎</div>
                    <p>No results for this filter yet.</p>
                  </div>
                </td>
              </tr>
            )}
            {visible.map((r, i) => (
              <tr key={`${r.doc_filename}-${i}`} className={i === visible.length - 1 && isLive ? 'row-new' : ''}>
                <td className="td-id">{r.doc_filename || r.row?.Customer_ID}</td>
                <td>
                  <span className={`decision-badge ${r.decision}`}>
                    {DECISION_ICON[r.decision] || '?'} {r.decision}
                  </span>
                </td>
                <td className="td-reason">{r.reasoning}</td>
                <td className="td-addr">{r.ocr_name_extracted || '—'}</td>
                <td className="td-addr">{r.ocr_address_extracted || '—'}</td>
                <td className="td-addr">{r.ocr_issue_date || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
