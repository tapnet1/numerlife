// Booking pages: times are listed in the practitioner's time zone. When the visitor's own zone
// differs at that moment, also show the time in theirs ("Your time: ...").
// Each time carries data-local: the practitioner's local time with its UTC offset (ISO 8601).
(function () {
  "use strict";
  var notes = document.querySelectorAll("[data-local-time]");
  if (!notes.length || !window.Intl || !Intl.DateTimeFormat) return;

  function practitionerOffset(iso) {
    var match = /([+-])(\d{2}):(\d{2})$/.exec(iso || "");
    if (!match) return null;
    var minutes = parseInt(match[2], 10) * 60 + parseInt(match[3], 10);
    return match[1] === "-" ? -minutes : minutes;
  }

  function update(note, iso) {
    var when = new Date(iso || "");
    var offset = practitionerOffset(iso);
    if (isNaN(when.getTime()) || offset === null || -when.getTimezoneOffset() === offset) {
      note.hidden = true;
      return;
    }
    var lang = note.getAttribute("data-lang") === "es" ? "es" : "en";
    note.textContent = note.getAttribute("data-label") + " " + new Intl.DateTimeFormat(lang, {
      weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(when);
    note.hidden = false;
  }

  notes.forEach(function (note) {
    if (note.hasAttribute("data-local")) {
      update(note, note.getAttribute("data-local"));
      return;
    }
    var form = note.closest("form");
    if (!form) return;
    form.addEventListener("change", function (event) {
      if (event.target && event.target.name === "slot") update(note, event.target.getAttribute("data-local"));
    });
  });
})();
