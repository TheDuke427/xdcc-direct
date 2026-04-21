import { useState } from 'react'
import { api } from '../api'
import styles from './AddDownloadForm.module.css'

const DEFAULTS = {
  server: '',
  port: '6667',
  ssl: false,
  nickname: 'xdccuser',
  channel: '',
  bot: '',
  pack: '',
}

export default function AddDownloadForm({ onAdded }) {
  const [form, setForm] = useState(DEFAULTS)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  function set(key, value) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const job = await api.addDownload({
        ...form,
        port: parseInt(form.port, 10),
      })
      onAdded(job)
      setForm(DEFAULTS)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <h2 className={styles.title}>New Download</h2>

      <div className={styles.row}>
        <div className={styles.field} style={{ flex: 3 }}>
          <label>IRC Server</label>
          <input
            required
            placeholder="irc.rizon.net"
            value={form.server}
            onChange={(e) => set('server', e.target.value)}
          />
        </div>
        <div className={styles.field} style={{ flex: 1 }}>
          <label>Port</label>
          <input
            type="number"
            min={1}
            max={65535}
            value={form.port}
            onChange={(e) => set('port', e.target.value)}
          />
        </div>
        <div className={styles.field} style={{ flex: 1, alignSelf: 'flex-end', paddingBottom: 2 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              style={{ width: 'auto' }}
              checked={form.ssl}
              onChange={(e) => set('ssl', e.target.checked)}
            />
            SSL
          </label>
        </div>
      </div>

      <div className={styles.row}>
        <div className={styles.field}>
          <label>Nickname</label>
          <input
            required
            placeholder="xdccuser"
            value={form.nickname}
            onChange={(e) => set('nickname', e.target.value)}
          />
        </div>
        <div className={styles.field}>
          <label>Channel (optional)</label>
          <input
            placeholder="#nibl"
            value={form.channel}
            onChange={(e) => set('channel', e.target.value)}
          />
        </div>
      </div>

      <div className={styles.row}>
        <div className={styles.field}>
          <label>Bot</label>
          <input
            required
            placeholder="Ginpachi-Sensei"
            value={form.bot}
            onChange={(e) => set('bot', e.target.value)}
          />
        </div>
        <div className={styles.field}>
          <label>Pack</label>
          <input
            required
            placeholder="#123"
            value={form.pack}
            onChange={(e) => set('pack', e.target.value)}
          />
        </div>
      </div>

      {error && <p className={styles.error}>{error}</p>}

      <button type="submit" className={styles.submit} disabled={loading}>
        {loading ? 'Queuing…' : 'Add Download'}
      </button>
    </form>
  )
}
