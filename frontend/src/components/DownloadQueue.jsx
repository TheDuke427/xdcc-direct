import { api } from '../api'
import styles from './DownloadQueue.module.css'

const STATUS_COLOR = {
  queued: '#94a3b8',
  connecting: '#fbbf24',
  downloading: '#6366f1',
  complete: '#4ade80',
  failed: '#f87171',
  cancelled: '#94a3b8',
}

function fmtBytes(n) {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`
  return `${(n / 1073741824).toFixed(2)} GB`
}

function fmtSpeed(bps) {
  if (!bps) return ''
  if (bps < 1024) return `${bps.toFixed(0)} B/s`
  if (bps < 1048576) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${(bps / 1048576).toFixed(1)} MB/s`
}

function fmtEta(secs) {
  if (secs == null || secs <= 0) return ''
  if (secs < 60) return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

export default function DownloadQueue({ jobs, onUpdate }) {
  async function cancel(id) {
    try { await api.cancelDownload(id) } catch {}
    onUpdate()
  }

  async function remove(id) {
    try { await api.deleteDownload(id) } catch {}
    onUpdate()
  }

  if (!jobs.length) {
    return <p className={styles.empty}>No downloads yet. Add one above.</p>
  }

  return (
    <div className={styles.list}>
      {jobs.map((job) => (
        <div key={job.id} className={styles.item}>
          <div className={styles.header}>
            <span
              className={styles.badge}
              style={{ background: STATUS_COLOR[job.status] + '22', color: STATUS_COLOR[job.status] }}
            >
              {job.status}
            </span>
            <span className={styles.target}>
              {job.bot} {job.pack} @ {job.server}:{job.port}
            </span>
            <span className={styles.actions}>
              {['queued', 'connecting', 'downloading'].includes(job.status) && (
                <button className={styles.btnCancel} onClick={() => cancel(job.id)}>Cancel</button>
              )}
              {['complete', 'failed', 'cancelled'].includes(job.status) && (
                <button className={styles.btnRemove} onClick={() => remove(job.id)}>Remove</button>
              )}
            </span>
          </div>

          {job.filename && (
            <div className={styles.filename}>{job.filename}</div>
          )}

          {job.status === 'downloading' && (
            <div className={styles.progressWrap}>
              <div className={styles.bar}>
                <div className={styles.fill} style={{ width: `${job.progress}%` }} />
              </div>
              <span className={styles.progressText}>
                {job.progress}% — {fmtBytes(job.received)} / {fmtBytes(job.total)}
                {job.speed > 0 && ` — ${fmtSpeed(job.speed)}`}
                {job.eta > 0 && ` — ${fmtEta(job.eta)} left`}
              </span>
            </div>
          )}

          {job.status === 'complete' && (
            <div className={styles.completeRow}>
              <span className={styles.filesize}>{fmtBytes(job.total)}</span>
              <a
                href={api.fileUrl(job.filename)}
                download={job.filename}
                className={styles.downloadLink}
              >
                Download file
              </a>
            </div>
          )}

          {job.status === 'failed' && (
            <div className={styles.error}>{job.error}</div>
          )}
        </div>
      ))}
    </div>
  )
}
