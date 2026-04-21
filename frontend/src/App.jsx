import { useState, useCallback } from 'react'
import AddDownloadForm from './components/AddDownloadForm'
import DownloadQueue from './components/DownloadQueue'
import FileLibrary from './components/FileLibrary'
import SearchTab from './components/SearchTab'
import IndexTab from './components/IndexTab'
import { useWebSocket } from './useWebSocket'
import { api } from './api'
import styles from './App.module.css'

export default function App() {
  const [jobs, setJobs] = useState([])
  const [tab, setTab] = useState('search')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)

  const handleWsMessage = useCallback((job) => {
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.id === job.id)
      if (idx === -1) return [...prev, job]
      const next = [...prev]
      next[idx] = job
      return next
    })
  }, [])

  useWebSocket(handleWsMessage)

  async function refresh() {
    try {
      setJobs(await api.listDownloads())
    } catch {}
  }

  function handleAdded(job) {
    setJobs((prev) => [...prev, job])
    setTab('queue')
  }

  const active = jobs.filter((j) => ['queued', 'connecting', 'downloading'].includes(j.status)).length

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1 className={styles.logo}>XDCC</h1>
        <span className={styles.subtitle}>Download Manager</span>
        {active > 0 && (
          <span className={styles.activeBadge}>{active} active</span>
        )}
      </header>

      <main className={styles.main}>
        <div className={styles.tabs}>
          <button
            className={tab === 'search' ? styles.tabActive : styles.tab}
            onClick={() => setTab('search')}
          >
            Search
          </button>
          <button
            className={tab === 'queue' ? styles.tabActive : styles.tab}
            onClick={() => setTab('queue')}
          >
            Queue {jobs.length > 0 && `(${jobs.length})`}
          </button>
          <button
            className={tab === 'files' ? styles.tabActive : styles.tab}
            onClick={() => setTab('files')}
          >
            Files
          </button>
          <button
            className={tab === 'index' ? styles.tabActive : styles.tab}
            onClick={() => setTab('index')}
          >
            Index
          </button>
        </div>

        <div className={styles.panel}>
          {tab === 'search' && (
            <SearchTab
              onDownloaded={() => setTab('queue')}
              query={searchQuery}
              onQueryChange={setSearchQuery}
              results={searchResults}
              onResultsChange={setSearchResults}
            />
          )}
          {tab === 'queue' && (
            <>
              <AddDownloadForm onAdded={handleAdded} />
              <DownloadQueue jobs={jobs} onUpdate={refresh} />
            </>
          )}
          {tab === 'files' && <FileLibrary />}
          {tab === 'index' && <IndexTab />}
        </div>
      </main>
    </div>
  )
}
