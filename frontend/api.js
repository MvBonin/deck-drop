const BASE = '';

async function req(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(BASE + path, opts);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw Object.assign(new Error(`${method} ${path} → ${r.status}`), { status: r.status, body: text });
  }
  if (r.status === 204) return null;
  return r.json();
}

const get  = (path)        => req('GET',    path);
const post = (path, body)  => req('POST',   path, body);
const put  = (path, body)  => req('PUT',    path, body);
const patch= (path, body)  => req('PATCH',  path, body);
const del  = (path)        => req('DELETE', path);

export const api = {
  status:       ()          => get('/api/status'),
  games:        ()          => get('/api/games'),
  addGame:      (body)      => post('/api/games', body),
  patchGame:    (id, body)  => patch(`/api/games/${id}`, body),
  removeGame:   (id)        => del(`/api/games/${id}`),
  magnet:       (id)        => get(`/api/games/${id}/magnet`),
  peers:        ()          => get('/api/peers'),
  netGames:     ()          => get('/api/network/games'),
  startDl:      (body)      => post('/api/download', body),
  downloads:    ()          => get('/api/downloads'),
  cancelDl:     (id)        => del(`/api/downloads/${id}`),
  settings:     ()          => get('/api/settings'),
  saveSettings: (body)      => put('/api/settings', body),
};

export function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  return (n / 1024 ** i).toFixed(i > 1 ? 1 : 0) + ' ' + u[i];
}

export function fmtSpeed(bps) {
  return fmtBytes(bps) + '/s';
}
