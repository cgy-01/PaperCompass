// Bounded response cache and per-view cancellation keep navigation responsive.
const cache = new Map(),
  pending = new Map();
export async function api(path, { channel = "", ttl = 30000 } = {}) {
  if (channel) {
    pending.get(channel)?.abort();
    pending.delete(channel);
  }
  const hit = cache.get(path);
  if (hit && Date.now() - hit.time < ttl) return hit.data;
  const controller = new AbortController();
  if (channel) pending.set(channel, controller);
  const timer = setTimeout(
    () =>
      controller.abort(new DOMException("请求超时，请重试", "TimeoutError")),
    15000,
  );
  try {
    const response = await fetch(path, { signal: controller.signal });
    if (!response.ok) {
      const error = new Error(`请求失败（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    const data = await response.json();
    cache.delete(path);
    cache.set(path, { data, time: Date.now() });
    while (cache.size > 40) cache.delete(cache.keys().next().value);
    return data;
  } finally {
    clearTimeout(timer);
    if (pending.get(channel) === controller) pending.delete(channel);
  }
}
