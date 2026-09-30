// JSON API client. Errors carry the server's {error} message.

async function request(method, path, { params, body } = {}) {
  let url = path;
  if (params) {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') usp.set(k, v);
    }
    const qs = usp.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  const init = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    const err = new Error((data && data.error) || `${res.status} ${res.statusText}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  get: (path, params) => request('GET', path, { params }),
  post: (path, body) => request('POST', path, { body: body || {} }),
};
