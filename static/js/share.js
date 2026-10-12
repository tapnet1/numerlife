// Share bar: native share sheet on phones, copy-link everywhere it's supported.
// The plain links (WhatsApp, Facebook, X, Email, Text) work without JavaScript.
(function () {
  document.querySelectorAll(".share-buttons").forEach(function (bar) {
    var url = bar.getAttribute("data-share-url");
    var text = bar.getAttribute("data-share-text");
    var status = bar.parentElement.querySelector(".share-status");
    var say = function (message) { if (status) { status.textContent = message; } };

    var nativeBtn = bar.querySelector(".share-native");
    if (nativeBtn && navigator.share) {
      nativeBtn.hidden = false;
      nativeBtn.addEventListener("click", function () {
        navigator.share({ title: document.title, text: text, url: url }).catch(function () {});
      });
    }

    var copyBtn = bar.querySelector(".share-copy");
    if (copyBtn && navigator.clipboard && window.isSecureContext) {
      copyBtn.hidden = false;
      copyBtn.addEventListener("click", function () {
        navigator.clipboard.writeText(url).then(function () {
          copyBtn.querySelector("span").textContent = "Copied!";
          say("Link copied. Paste it anywhere to share.");
          setTimeout(function () { copyBtn.querySelector("span").textContent = "Copy link"; }, 2500);
        }, function () { say("Couldn't copy automatically. The link is " + url); });
      });
    }
  });
})();
