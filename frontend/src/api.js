const BASE = '/api'

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  listDownloads:    () => request('GET', '/downloads'),
  addDownload:      (data) => request('POST', '/downloads', data),
  cancelDownload:   (id) => request('POST', `/downloads/${id}/cancel`),
  deleteDownload:   (id) => request('DELETE', `/downloads/${id}`),
  searchXdcc:       (q) => request('GET', `/search?q=${encodeURIComponent(q)}`),
  packList:         (data) => request('POST', '/packlist', data),
  listFiles:        () => request('GET', '/files'),
  fileUrl:          (name) => `${BASE}/files/${encodeURIComponent(name)}`,
  indexStatus:      () => request('GET', '/index/status'),
  listChannels:     () => request('GET', '/index/channels'),
  addChannel:       (data) => request('POST', '/index/channels', data),
  removeChannel:    (id) => request('DELETE', `/index/channels/${id}`),
  purgeChannelPacks:(id) => request('DELETE', `/index/channels/${id}/packs`),
  getVpnStatus:     () => request('GET', '/vpn/status'),
  setVpnStatus:     (status) => request('PUT', '/vpn/status', { status }),
}
