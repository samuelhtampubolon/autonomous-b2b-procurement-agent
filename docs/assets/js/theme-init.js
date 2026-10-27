/* Runs synchronously in <head> to apply the saved theme before first paint. */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.remove("no-js");
  var theme = "dark";
  try {
    var saved = window.localStorage.getItem("pa.theme");
    if (saved === "light" || saved === "dark") theme = saved;
  } catch (e) {
    /* storage unavailable (private mode / blocked): keep default */
  }
  root.setAttribute("data-theme", theme);
})();
