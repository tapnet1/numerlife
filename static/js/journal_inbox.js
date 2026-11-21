// Journal inbox: change journal date without a button.
document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
  select.addEventListener("change", function () { select.form.submit(); });
});
