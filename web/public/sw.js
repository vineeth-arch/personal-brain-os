/* Brain Cockpit app-shell service worker.
 *
 * Contract (CLAUDE.md / the PWA spec): API data is NEVER cached — the cockpit
 * always shows live pipeline state. Two guards enforce that here: cross-origin
 * requests are ignored entirely (the API normally lives on another origin),
 * and any same-origin path containing /api/ (dev proxy or a future same-origin
 * deploy) is ignored too. The fetch client adds cache:'no-store' as a third.
 *
 * Static shell assets are safe to cache aggressively: Vite content-hashes
 * them, so a new build means new URLs. Bump VERSION on SW logic changes.
 */
const VERSION = "cockpit-shell-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // API requests never enter the service worker's cache path.
  if (url.origin !== self.location.origin || url.pathname.includes("/api/")) return;

  // Navigations: network-first so deploys land, cached shell as offline fallback.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((cache) => cache.put("index.html", copy));
          return res;
        })
        .catch(() => caches.match("index.html")),
    );
    return;
  }

  // Hashed static assets, fonts, icons: cache-first, populate on miss.
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return res;
        }),
    ),
  );
});
