import { useRef, useState, useCallback } from 'react'

export default function FileUpload({ file, onFileSelect, disabled, accept = '.csv', hint }) {
  const inputRef  = useRef(null)
  const [dragging, setDragging] = useState(false)

  const handleFile = useCallback((f) => {
    if (!f) return
    const ext = '.' + f.name.split('.').pop().toLowerCase()
    const acceptedExts = accept.split(',').map(s => s.trim())
    if (!acceptedExts.includes(ext)) {
      alert(`Please select a file of type: ${accept}`)
      return
    }
    onFileSelect(f)
  }, [onFileSelect, accept])

  const onDrop = useCallback((e) => {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    handleFile(e.dataTransfer.files[0])
  }, [disabled, handleFile])

  const zoneClass = [
    'upload-zone',
    dragging ? 'drag-over' : '',
    file     ? 'has-file'  : '',
  ].filter(Boolean).join(' ')

  return (
    <div
      className={zoneClass}
      onClick={() => !disabled && inputRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="upload-icon">{file ? '📄' : '📂'}</div>
      <div className="upload-label">
        {file ? 'File selected' : 'Drop your CSV here or click to browse'}
      </div>
      <div className="upload-hint">
        {hint || 'Required columns: Customer_ID · Registered_Name · Registered_Address · Phone · Email · Date_of_Birth · IPs'}
      </div>
      {file && <div className="upload-filename">✓ {file.name}</div>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: 'none' }}
        onChange={(e) => handleFile(e.target.files[0])}
        disabled={disabled}
      />
    </div>
  )
}
