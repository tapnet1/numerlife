// Day journal on the client's calendar: open the note form for the day that was tapped,
// and send the browser's time zone so each note shows the time it was written.
(function () {
  var tz = "";
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { tz = ""; }
  document.querySelectorAll(".journal-tz").forEach(function (input) { input.value = tz; });

  function openTarget() {
    var id = window.location.hash.slice(1);
    if (!/^d\d{1,2}$/.test(id)) return;
    var day = document.getElementById(id);
    var details = day && day.querySelector(".journal-add");
    if (details) details.open = true;
  }
  window.addEventListener("hashchange", openTarget);
  openTarget();
})();
