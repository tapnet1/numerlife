/* numer.life service worker: push notifications for new leads.
   No offline caching of pages: lead data is private and must always be fresh. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "numer.life", body: event.data && event.data.text() }; }
  var title = data.title || "numer.life";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    icon: "/static/img/icon-192.png",
    badge: "/static/img/icon-192.png",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    data: { url: data.url || "/dashboard" }
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = new URL((event.notification.data && event.notification.data.url) || "/dashboard", self.location.origin);
  if (target.origin !== self.location.origin) { target = new URL("/dashboard", self.location.origin); }
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
    for (var i = 0; i < clients.length; i++) {
      if (new URL(clients[i].url).origin === self.location.origin && "focus" in clients[i]) {
        return clients[i].navigate(target.href).then(function (c) { return (c || clients[i]).focus(); });
      }
    }
    return self.clients.openWindow(target.href);
  }));
});
