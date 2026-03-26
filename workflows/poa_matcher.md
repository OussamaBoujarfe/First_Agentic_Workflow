# Proof of Address (PoA) Verification — Agent SOP

## Operational Assumptions

A human compliance agent has already **pre-screened the document**:
- It is genuine (not fake, not tampered)
- It is an accepted document type (e.g. utility bill, bank statement, lease agreement)
  - Mobile phone bills are **not** accepted
  - Fintech-only statements (e.g. Revolut) may be **not** accepted depending on jurisdiction policy

The AI system's **only job** is:
1. Extract structured fields from the document
2. Auto-translate/transliterate non-Latin addresses to English
3. Flag anomalies for the human agent

**The human agent makes all final decisions.** Decision states:
- **Pass** — name and address match the registered customer record
- **Failed** — clear mismatch between document and registered data
- **Needs More Information** — 50–70% of address data is missing, illegible, or ambiguous

---

## Extracted Fields by Document Type

### Standard Documents (utility bills, bank statements, etc.)
| Field | Description |
|---|---|
| `customer_name` | Account holder / billed-to name on the document |
| `customer_address` | Residential or billing address of the customer |
| `issue_date` | Invoice date, statement date, or bill date |
| `is_po_box` | `true` if the customer address contains a PO Box |
| `address_transliterated` | `true` if address was auto-translated from a non-Latin script |
| `address_original` | Original non-Latin version of the address (if transliterated) |

### Lease Agreements (rental contracts, tenancy agreements)
| Field | Description |
|---|---|
| `tenant_name` | Tenant / lessee name |
| `landlord_name` | Landlord / lessor name |
| `both_signed` | `true` if signatures from both parties are present, `false` otherwise |
| `lease_duration` | Stay period (e.g. "12 months, 01 Jan 2026 – 31 Dec 2026") |
| `customer_address` | Leased property address |
| `issue_date` | Lease start date or signing date |

---

## Flags Raised Automatically (no human input required)

These flags are computed from the extracted data and surfaced in the UI for the agent's attention.

| Flag | Condition | Guidance |
|---|---|---|
| **PO Box detected** | `is_po_box: true` | PO Boxes are typically unacceptable as residential addresses. Mark as **Failed** unless policy allows exceptions. |
| **Unsigned lease** | `both_signed: false` | Incomplete document — at minimum one signature is missing. Mark as **Needs More Information** and request a countersigned version. |
| **Address transliterated** | `address_transliterated: true` | Address was in a non-Latin script and has been automatically translated to English. The `address_original` field shows the source text for verification if needed. |

---

## Comparison Guide for Human Agents

### Name Matching
Compare `customer_name` (or `tenant_name` for leases) against `Registered_Name`:

| Scenario | Guidance |
|---|---|
| Exact match (case-insensitive) | Matching |
| Diacritics stripped (João = Joao) | Matching |
| Common nickname (Jon = Jonathan, Bob = Robert) | Matching |
| First initial + full surname (S. Martin = Sophie Martin) | Likely matching — check address |
| Non-Latin romanisation already applied by system | Review `address_original` to confirm fidelity |
| Different first name, same surname | Flag — could be a family member, not the customer |
| Completely different name | **Failed** |
| Partially illegible / only initials | **Needs More Information** |

### Address Matching
Compare `customer_address` against `Registered_Address`:

| Scenario | Guidance |
|---|---|
| Exact match | Matching |
| Standard abbreviations (St = Street, Ave = Avenue, Blvd = Boulevard) | Matching |
| Diacritics stripped (Élysées = Elysees) | Matching |
| Minor formatting difference (flat number order, missing comma) | Matching |
| Missing postal code but street + city match | Likely matching — accept with note |
| Wrong street number | **Failed** |
| Different street name | **Failed** |
| PO Box address | **Failed** (see flag above) |
| 50–70% of address data missing or illegible | **Needs More Information** |

### Lease Agreement Specifics
- Compare `tenant_name` against `Registered_Name`
- Compare the leased property address against `Registered_Address`
- If `both_signed: false` → do not accept, mark **Needs More Information**
- If `lease_duration` is expired → flag for policy check before accepting

---

## Address Transliteration Reference

The system auto-transliterates the following scripts to English Latin:

| Script | Example |
|---|---|
| Chinese (Simplified/Traditional) | 北京市朝阳区 → Beijing, Chaoyang District |
| Japanese (Kanji/Kana) | 東京都渋谷区 → Tokyo, Shibuya-ku |
| Korean (Hangul) | 서울특별시 강남구 → Seoul, Gangnam-gu |
| Arabic | شارع الملك فهد → King Fahd Street |
| Cyrillic (Russian/Ukrainian/etc.) | ул. Тверская → Tverskaya St. |
| Hindi (Devanagari) | मुंबई → Mumbai |
| Urdu | کراچی → Karachi |
| Persian/Farsi | تهران → Tehran |

If the transliteration looks incorrect or ambiguous, the `address_original` field is always available for manual review.

---

## Decision Checklist

Before submitting a decision, confirm:

- [ ] Customer name matches (or has an acceptable variation)
- [ ] Customer address matches the registered address
- [ ] Document is not expired (issue date is within acceptable window — typically 3 months)
- [ ] No PO Box in customer address
- [ ] For leases: both parties signed, lease covers the current period
- [ ] If address was transliterated: spot-check `address_original` looks credible
