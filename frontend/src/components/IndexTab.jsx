import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import styles from './IndexTab.module.css'

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString()
}

export default function IndexTab() {
  const [status, setStatus] = useState(null)
  const [error, setError]   = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm]     = useState({ network: '', server: '', port: '6667', ssl: false, channel: '' })
  const [formErr, setFormErr] = useState('')

  const load = useCallback(async () => {
    try {
      setStatus(await api.indexStatus())
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  async function handleAdd(e) {
    e.preventDefault()
    setFormErr('')
    if (!form.network || !form.server || !form.channel) {
      setFormErr('Network, server and channel are required')
      return
    }
    setAdding(true)
    try {
      await api.addChannel({ ...form, port: parseInt(form.port, 10) })
      setForm({ network: '', server: '', port: '6667', ssl: false, channel: '' })
      await load()
    } catch (e) {
      setFormErr(e.message)
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(id) {
    try {
      await api.removeChannel(id)
      await load()
    } catch (e) {
      setError(e.message)
    }
  }

  if (error) return <p className={styles.error}>{error}</p>
  if (!status) return <p className={styles.loading}>Loading…</p>

  const channels = status.channels ?? []

  return (
    <div className={styles.wrap}>
      <div className={styles.statsRow}>
        <span className={styles.stat}>
          <span className={styles.statNum}>{status.fresh_packs?.toLocaleString() ?? 0}</span>
          <span className={styles.statLabel}>packs indexed</span>
        </span>
        <span className={styles.stat}>
          <span className={styles.statNum}>{channels.filter(c => c.connected).length}/{channels.length}</span>
          <span className={styles.statLabel}>channels connected</span>
        </span>
      </div>

      <table className={styles.table}>
        <thead>
          <tr>
            <th>Network</th>
            <th>Channel</th>
            <th>Server</th>
            <th>Status</th>
            <th>Packs seen</th>
            <th>Last activity</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {channels.map(ch => (
            <tr key={ch.id}>
              <td className={styles.network}>{ch.network}</td>
              <td className={styles.channel}>{ch.channel}</td>
              <td className={styles.server}>{ch.server}:{ch.port}</td>
              <td>
                <span className={ch.connected ? styles.dot_on : styles.dot_off} />
                {ch.connected ? 'connected' : 'connecting…'}
              </td>
              <td className={styles.num}>{ch.packs_seen.toLocaleString()}</td>
              <td className={styles.time}>{fmt(ch.last_activity)}</td>
              <td>
                <button className={styles.removeBtn} onClick={() => handleRemove(ch.id)}>✕</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className={styles.addSection}>
        <div className={styles.addTitle}>Add channel</div>
        <form className={styles.addForm} onSubmit={handleAdd}>
          <input
            className={styles.input}
            placeholder="Network (e.g. Rizon)"
            value={form.network}
            onChange={e => setForm(f => ({ ...f, network: e.target.value }))}
          />
          <input
            className={styles.input}
            placeholder="Server (e.g. irc.rizon.net)"
            value={form.server}
            onChange={e => setForm(f => ({ ...f, server: e.target.value }))}
          />
          <input
            className={`${styles.input} ${styles.portInput}`}
            placeholder="Port"
            value={form.port}
            onChange={e => setForm(f => ({ ...f, port: e.target.value }))}
          />
          <input
            className={styles.input}
            placeholder="Channel (e.g. #elitewarez)"
            value={form.channel}
            onChange={e => setForm(f => ({ ...f, channel: e.target.value }))}
          />
          <label className={styles.sslLabel}>
            <input
              type="checkbox"
              checked={form.ssl}
              onChange={e => setForm(f => ({ ...f, ssl: e.target.checked }))}
            />
            SSL
          </label>
          <button className={styles.addBtn} type="submit" disabled={adding}>
            {adding ? '…' : 'Add'}
          </button>
        </form>
        {formErr && <p className={styles.formErr}>{formErr}</p>}
      </div>
    </div>
  )
}
