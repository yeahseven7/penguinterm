/* PenguinTerm — minimal fetch helper. Throws Error with .status and .detail. */
'use strict';

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, options = {}) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (options.body !== undefined) {
    if (options.formData) {
      opts.body = options.body; // browser sets multipart boundary
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(options.body);
    }
  }
  const resp = await fetch(path, opts);
  let data = null;
  const text = await resp.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!resp.ok) {
    const detail = (data && (data.detail || data.message)) || `HTTP ${resp.status}`;
    if (resp.status === 401 && !options.allow401) {
      window.dispatchEvent(new CustomEvent('pt:unauthorized'));
    }
    throw new ApiError(resp.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

const api = {
  get: (path, opts) => request('GET', path, opts),
  post: (path, body, opts) => request('POST', path, Object.assign({ body }, opts)),
  put: (path, body, opts) => request('PUT', path, Object.assign({ body }, opts)),
  patch: (path, body, opts) => request('PATCH', path, Object.assign({ body }, opts)),
  del: (path, opts) => request('DELETE', path, opts),

  /* Upload with progress callback (XHR; fetch has no upload progress). */
  upload(path, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', path);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError(xhr.status, (data && data.detail) || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'));
      xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'));
      const form = new FormData();
      form.append('file', file, file.name);
      xhr.send(form);
    });
  },
};

window.api = api;
window.ApiError = ApiError;
