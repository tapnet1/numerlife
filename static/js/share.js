// Lead page: share the client page link (native share panel on phones; copy fallback).
(function () {
  document.querySelectorAll(".share-row").forEach(function (row) {
    var button = row.querySelector("[data-share]");
    var data = {title: row.dataset.shareTitle, text: row.dataset.shareText, url: row.dataset.shareUrl};
    if (button && navigator.share && (!navigator.canShare || navigator.canShare(data))) {
      button.hidden = false;
      button.addEventListener("click", function () {
        navigator.share(data).catch(function () { /* closed or not allowed: nothing to do */ });
      });
    }
  });
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.dataset.copy, label = button.textContent;
      function done() { button.textContent = "Copied ✓"; setTimeout(function () { button.textContent = label; }, 2000); }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      } else {
        var area = document.createElement("textarea");
        area.value = text; document.body.appendChild(area); area.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
        document.body.removeChild(area);
      }
    });
  });
})();
