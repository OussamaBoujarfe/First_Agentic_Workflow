const CARDS = [
  { key: 'Pass',     label: 'Pass',     icon: '✅', cls: 'pass'     },
  { key: 'Fail',     label: 'Fail',     icon: '❌', cls: 'fail'     },
  { key: 'Escalate', label: 'Escalate', icon: '⚠️', cls: 'escalate' },
  { key: 'ERROR',    label: 'Error',    icon: '🔴', cls: 'error'    },
]

export default function SummaryCards({ counts, total }) {
  return (
    <div className="summary-grid">
      {CARDS.map(({ key, label, icon, cls }) => {
        const n   = counts[key] || 0
        const pct = total > 0 ? Math.round((n / total) * 100) : 0
        return (
          <div key={key} className={`summary-card ${cls}`}>
            <div className="sc-label">{icon} {label}</div>
            <div className="sc-count">{n}</div>
            <div className="sc-pct">{pct}% of {total}</div>
          </div>
        )
      })}
    </div>
  )
}
