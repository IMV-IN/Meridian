// Where to send someone after sign-in (?next=). Same-origin paths only: no open redirect.

/** `value` as a same-origin path (with its query and fragment), or null. The browser's URL parser drops
 * TAB, CR and LF and treats `\` like `/`, so "/\t/evil.com" would become "//evil.com": anything with a
 * control character or a backslash is refused, and what is left must resolve to this origin. */
export function safeReturnPath(value: string | null, origin: string = window.location.origin): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f-\u009f]/.test(value)) return null
  let u: URL
  try {
    u = new URL(value, origin)
  } catch {
    return null
  }
  return u.origin === origin ? u.pathname + u.search + u.hash : null
}
