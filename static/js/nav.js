// Mobile header menu: the hamburger opens and closes the account links.
(function () {
  var toggle = document.querySelector(".menu-toggle");
  var nav = document.getElementById("account-nav");
  if (!toggle || !nav) { return; }
  function set(open) {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    nav.classList.toggle("is-open", open);
  }
  toggle.addEventListener("click", function () { set(toggle.getAttribute("aria-expanded") !== "true"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { set(false); } });
  document.addEventListener("click", function (e) {
    if (!nav.contains(e.target) && !toggle.contains(e.target)) { set(false); }
  });
})();
