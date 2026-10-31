// cache simples pra o app abrir sem internet (só vale quando o app está hospedado em https)
const V = "previsoes-v1", ARQ = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(V).then(c => c.addAll(ARQ))));
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))));
self.addEventListener("fetch", e => e.respondWith(fetch(e.request).catch(() => caches.match(e.request))));
