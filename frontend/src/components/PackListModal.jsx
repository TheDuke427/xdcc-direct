import { useState, useEffect } from 'react'
import { api } from '../api'
import styles from './PackListModal.module.css'

export default function PackListModal({ botInfo, onClose, onDownloaded }) {
  const [packs, setPacks] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [getting, setGetting] = useState(null)

  useEffect(() => {
    api.packList(botInfo)
      .then(setPacks)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleGet(pack) {
    setGetting(pack.pack)
    try {
      await api.addDownload({ ...botInfo, pack: pack.pack })
      onDownloaded()
      onClose()
    } catch (e) {
      setError('Failed to queue: ' + e.message)
      setGetting(null)
    }
  }

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <span className={styles.title}>{botInfo.bot}</span>
          <span className={styles.sub}>{botInfo.server} {botInfo.channel}</span>
          <button className={styles.close} onClick={onClose}>✕</button>
        </div>

        {loading && <p className={styles.status}>Fetching pack list…</p>}
        {error && <p className={styles.error}>{error}</p>}

        {packs !== null && packs.length === 0 && !loading && (
          <p className={styles.status}>No packs returned (bot may not support xdcc list)</p>
        )}

        {packs && packs.length > 0 && (
          <div className={styles.tableWrap}>
            <div className={styles.count}>{packs.length} packs</div>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Filename</th>
                  <th>Size</th>
                  <th>Gets</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {packs.map((p) => (
                  <tr key={p.pack}>
                    <td className={styles.pack}>{p.pack}</td>
                    <td className={styles.filename}>{p.filename}</td>
                    <td className={styles.size}>{p.size}</td>
                    <td className={styles.gets}>{p.gets}x</td>
                    <td>
                      <button
                        className={styles.getBtn}
                        disabled={getting !== null}
                        onClick={() => handleGet(p)}
                      >
                        {getting === p.pack ? '…' : 'Get'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
