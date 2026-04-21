import { useEffect, useState } from 'react'
import { api } from '../api'
import styles from './FileLibrary.module.css'

function fmtBytes(n) {
  if (!n) return '0 B'
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`
  return `${(n / 1073741824).toFixed(2)} GB`
}

export default function FileLibrary() {
  const [files, setFiles] = useState([])

  async function load() {
    try {
      setFiles(await api.listFiles())
    } catch {}
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  if (!files.length) {
    return <p className={styles.empty}>No completed files yet.</p>
  }

  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Filename</th>
          <th>Size</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {files.map((f) => (
          <tr key={f.name}>
            <td className={styles.name}>{f.name}</td>
            <td className={styles.size}>{fmtBytes(f.size)}</td>
            <td>
              <a
                href={api.fileUrl(f.name)}
                download={f.name}
                className={styles.link}
              >
                Download
              </a>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
