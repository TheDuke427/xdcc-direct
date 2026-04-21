import { useState } from 'react'
import { api } from '../api'
import styles from './SearchTab.module.css'

export default function SearchTab({ onDownloaded, query, onQueryChange, results, onResultsChange }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [getting, setGetting] = useState(null)

  async function handleSearch(e) {
    e?.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError('')
    try {
      onResultsChange(await api.searchXdcc(query))
    } catch (err) {
      setError('Search failed: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleGet(r) {
    const key = `${r.bot}|${r.pack}`
    setGetting(key)
    try {
      await api.addDownload({
        server: r.server,
        port: r.port,
        ssl: false,
        nickname: 'xdccuser',
        channel: r.channel,
        bot: r.bot,
        pack: r.pack,
      })
      onDownloaded()
    } catch (err) {
      setError('Failed to queue: ' + err.message)
    } finally {
      setGetting(null)
    }
  }

  return (
    <div className={styles.wrap}>
      <form className={styles.searchBar} onSubmit={handleSearch}>
        <input
          className={styles.input}
          placeholder="Search filenames…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          autoFocus
        />
        <button type="submit" className={styles.btn} disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {error && <p className={styles.error}>{error}</p>}

      {results === null && !loading && (
        <p className={styles.hint}>Searches the sunxdcc.com index</p>
      )}

      {results !== null && results.length === 0 && (
        <p className={styles.empty}>No results for "{query}"</p>
      )}

      {results !== null && results.length > 0 && (
        <div className={styles.tableWrap}>
          <div className={styles.count}>{results.length} result{results.length !== 1 ? 's' : ''}</div>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Filename</th>
                <th>Size</th>
                <th>Bot / Pack</th>
                <th>Channel</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => {
                const key = `${r.bot}|${r.pack}`
                return (
                  <tr key={i}>
                    <td className={styles.filename}>{r.filename}</td>
                    <td className={styles.size}>{r.size || '—'}</td>
                    <td className={styles.bot}>{r.bot} <span className={styles.pack}>{r.pack}</span></td>
                    <td className={styles.channel}>{r.channel || '—'}</td>
                    <td>
                      <button
                        className={styles.getBtn}
                        disabled={getting !== null}
                        onClick={() => handleGet(r)}
                      >
                        {getting === key ? '…' : 'Get'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
