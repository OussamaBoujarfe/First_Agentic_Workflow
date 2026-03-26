import { useRef, useState, useCallback } from 'react'

const ACCEPTED_TYPES = ['.jpg', '.jpeg', '.png', '.pdf', '.json']
const ACCEPTED_MIME  = ['image/jpeg', 'image/png', 'application/pdf', 'application/json', 'text/plain']

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isValidFile(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase()
  return ACCEPTED_TYPES.includes(ext) || ACCEPTED_MIME.includes(file.type)
}

export default function DocumentUpload({ files, onChange, disabled }) {
  const inputRef  = useRef(null)
  const [dragging, setDragging] = useState(false)

  const addFiles = useCallback((newFiles) => {
    const valid = Array.from(newFiles).filter(isValidFile)
    const invalid = Array.from(newFiles).filter(f => !isValidFile(f))
    if (invalid.length > 0) {
      alert(`Unsupported file type(s): ${invalid.map(f => f.name).join(', ')}\nAccepted: ${ACCEPTED_TYPES.join(', ')}`)
    }
    if (valid.length > 0) {
      // Deduplicate by filename
      const existing = new Set(files.map(f => f.name))
      const unique = valid.filter(f => !existing.has(f.name))
      onChange([...files, ...unique])
    }
  }, [files, onChange])

  const removeFile = useCallback((name) => {
    onChange(files.filter(f => f.name !== name))
  }, [files, onChange])

  const onDrop = useCallback((e) => {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    addFiles(e.dataTransfer.files)
  }, [disabled, addFiles])

  const zoneClass = [
    'upload-zone',
    dragging ? 'drag-over' : '',
    files.length > 0 ? 'has-file' : '',
  ].filter(Boolean).join(' ')

  return (
    <div>
      <div
        className={zoneClass}
        onClick={() => !disabled && inputRef.current.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div className="upload-icon">📁</div>
        <div className="upload-label">
          {files.length > 0
            ? `${files.length} document${files.length !== 1 ? 's' : ''} selected — drop more to add`
            : 'Drop proof-of-address documents here or click to browse'}
        </div>
        <div className="upload-hint">
          Accepted: JPG · PNG · PDF · JSON &nbsp;·&nbsp; Multiple files allowed
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES.join(',')}
          style={{ display: 'none' }}
          onChange={(e) => addFiles(e.target.files)}
          disabled={disabled}
        />
      </div>

      {files.length > 0 && (
        <ul className="doc-list">
          {files.map((file) => {
            const ext = file.name.split('.').pop().toUpperCase()
            return (
              <li key={file.name} className="doc-list-item">
                <span className="doc-ext-badge">{ext}</span>
                <span className="doc-name">{file.name}</span>
                <span className="doc-size">{formatBytes(file.size)}</span>
                {!disabled && (
                  <button
                    className="doc-remove-btn"
                    onClick={() => removeFile(file.name)}
                    title="Remove"
                  >
                    ✕
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
