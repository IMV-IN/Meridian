// Apply the saved theme before first paint (no light/dark flash). A file, not
// an inline script, so the console runs under a strict script-src 'self' CSP.
try {
  var t = localStorage.getItem("meridian.console.theme") || "system"
  var d = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.classList.toggle("dark", d)
  document.documentElement.style.colorScheme = d ? "dark" : "light"
} catch (e) {}
