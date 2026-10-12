// Dashboard: install the app and turn on notifications for new leads.
(function () {
  var panel = document.querySelector(".notify-panel");
  if (!("serviceWorker" in navigator)) { if (panel) { panel.querySelector(".notify-status").textContent = "This browser doesn't support app notifications."; } return; }
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  if (!panel) { return; }

  var key = panel.getAttribute("data-vapid");
  var csrf = panel.getAttribute("data-csrf");
  var status = panel.querySelector(".notify-status");
  var onBtn = panel.querySelector(".notify-on");
  var offBtn = panel.querySelector(".notify-off");
  var testBtn = panel.querySelector(".notify-test");
  var installBtn = panel.querySelector(".install-app");
  var iosHint = panel.querySelector(".ios-hint");
  var say = function (t) { status.textContent = t; };
  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }).then(function (j) { if (!r.ok || !j.ok) { throw new Error(j.error || "Something went wrong."); } return j; }); });
  }
  function keyBytes(b64) {
    var pad = "=".repeat((4 - b64.length % 4) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) { out[i] = raw.charCodeAt(i); }
    return out;
  }
  function show(on) { onBtn.hidden = on; offBtn.hidden = !on; testBtn.hidden = !on; }

  var supported = "PushManager" in window && "Notification" in window && key;
  if (!supported) {
    if (isIOS && !standalone) { iosHint.hidden = false; say("On iPhone and iPad, add numer.life to your Home Screen first, then open it from there to turn on notifications."); }
    else if (!key) { say("Notifications aren't set up on the server yet."); }
    else { say("This browser doesn't support notifications."); }
    return;
  }

  navigator.serviceWorker.ready.then(function (reg) {
    return reg.pushManager.getSubscription().then(function (sub) {
      show(Boolean(sub) && Notification.permission === "granted");
      say(sub && Notification.permission === "granted" ? "Notifications are on for this device." :
          Notification.permission === "denied" ? "Notifications are blocked for this site. Allow them in your browser settings, then reload." :
          "Get an alert the moment a new lead unlocks their report.");
      onBtn.hidden = Boolean(sub) && Notification.permission === "granted";
      onBtn.addEventListener("click", function () {
        say("Asking for permission…");
        Notification.requestPermission().then(function (permission) {
          if (permission !== "granted") { throw new Error("Permission wasn't granted. You can allow notifications in your browser settings."); }
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
        }).then(function (sub) {
          var json = sub.toJSON();
          json.device = navigator.userAgent;
          return post("/push/subscribe", json);
        }).then(function () { show(true); say("Notifications are on for this device. Try a test!"); })
          .catch(function (e) { say(e.message); });
      });
      offBtn.addEventListener("click", function () {
        reg.pushManager.getSubscription().then(function (sub) {
          if (!sub) { return; }
          var endpoint = sub.endpoint;
          return sub.unsubscribe().then(function () { return post("/push/unsubscribe", { endpoint: endpoint }); });
        }).then(function () { show(false); say("Notifications are off for this device."); }).catch(function (e) { say(e.message); });
      });
      testBtn.addEventListener("click", function () {
        say("Sending a test…");
        post("/push/test").then(function () { say("Test sent. It should arrive in a few seconds."); }).catch(function (e) { say(e.message); });
      });
    });
  });

  // Install prompt (Chrome, Edge, Android)
  var deferred = null;
  window.addEventListener("beforeinstallprompt", function (e) { e.preventDefault(); deferred = e; if (installBtn && !standalone) { installBtn.hidden = false; } });
  if (installBtn) {
    installBtn.addEventListener("click", function () {
      if (!deferred) { return; }
      deferred.prompt();
      deferred.userChoice.finally(function () { deferred = null; installBtn.hidden = true; });
    });
  }
  if (isIOS && !standalone && iosHint) { iosHint.hidden = false; }
})();
