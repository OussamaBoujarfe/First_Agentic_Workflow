import { useState, useEffect } from 'react'

// Support both naming conventions (Phone/Phone_Number, Email/Email_Address, IPs/IP_Address)
function get(row, ...keys) {
  for (const k of keys) if (row[k] !== undefined && row[k] !== '') return row[k]
  return ''
}

// Convert ISO-3166-1 alpha-2 country code to emoji flag (e.g. "GB" → 🇬🇧)
function countryFlag(code) {
  if (!code || code.length !== 2) return ''
  const offset = 0x1F1E6 - 65 // regional indicator A starts at U+1F1E6
  return String.fromCodePoint(
    code.toUpperCase().charCodeAt(0) + offset,
    code.toUpperCase().charCodeAt(1) + offset,
  )
}

export default function CustomerProfile({ customer, onClear }) {
  const [ipGeo, setIpGeo] = useState({}) // { "1.2.3.4": { country, countryCode } }

  const ipRaw = get(customer, 'IPs', 'IP_Address', 'IP_Addresses')
  const ips = ipRaw ? ipRaw.split(/[,;]/).map(ip => ip.trim()).filter(Boolean) : []

  // Fetch geolocation for all IPs whenever the customer changes
  useEffect(() => {
    if (!ips.length) { setIpGeo({}); return }
    setIpGeo({})

    fetch('http://ip-api.com/batch?fields=query,country,countryCode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ips.map(ip => ({ query: ip }))),
    })
      .then(r => r.json())
      .then(data => {
        const geo = {}
        data.forEach(item => {
          if (item.query) geo[item.query] = { country: item.country, countryCode: item.countryCode }
        })
        setIpGeo(geo)
      })
      .catch(() => {}) // flags are decorative — silently ignore network errors
  }, [ipRaw]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!customer) return null

  const initial = (customer.Registered_Name || customer.Customer_ID || '?')[0].toUpperCase()

  return (
    <div className="profile-card">
      <div className="profile-header">
        <div className="profile-avatar">{initial}</div>
        <div className="profile-header-info">
          <div className="profile-name">{customer.Registered_Name}</div>
          <span className="profile-id-badge">{customer.Customer_ID}</span>
        </div>
        <button className="profile-clear-btn" onClick={onClear} title="Clear customer">✕</button>
      </div>

      <div className="profile-grid">
        <div className="profile-field">
          <div className="profile-label">Registered Address</div>
          <div className="profile-value">{customer.Registered_Address || '—'}</div>
        </div>

        <div className="profile-field">
          <div className="profile-label">Date of Birth</div>
          <div className="profile-value">{get(customer, 'Date_of_Birth') || '—'}</div>
        </div>

        <div className="profile-field">
          <div className="profile-label">Phone</div>
          <div className="profile-value profile-mono">{get(customer, 'Phone', 'Phone_Number') || '—'}</div>
        </div>

        <div className="profile-field">
          <div className="profile-label">Email</div>
          <div className="profile-value">{get(customer, 'Email', 'Email_Address') || '—'}</div>
        </div>

        <div className="profile-field profile-field-full">
          <div className="profile-label">Known IPs</div>
          <div className="profile-ips">
            {ips.length > 0
              ? ips.map(ip => {
                  const geo = ipGeo[ip]
                  const flag = geo ? countryFlag(geo.countryCode) : ''
                  const title = geo ? `${ip} — ${geo.country}` : ip
                  return (
                    <span key={ip} className="ip-chip" title={title}>
                      {flag && <span className="ip-flag">{flag}</span>}
                      {ip}
                    </span>
                  )
                })
              : <span className="profile-value">—</span>
            }
          </div>
        </div>
      </div>
    </div>
  )
}
