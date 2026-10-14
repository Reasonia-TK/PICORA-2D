/** CSSトークンをCanvas/WebGLが読める実色へ解決する。 */
let colorProbe: HTMLSpanElement | null = null;
export function resolveColor(value: string): string {
  if (!value.includes("var(") && !value.includes("color-mix(")) return value;
  const direct = /^var\((--[\w-]+)\)$/.exec(value.trim());
  const raw = direct ? getComputedStyle(document.documentElement).getPropertyValue(direct[1]).trim() : value;
  if (!raw.includes("var(") && !raw.includes("color-mix(")) return raw;
  if (!colorProbe || !colorProbe.isConnected) {
    colorProbe = document.createElement("span");
    colorProbe.hidden = true;
    colorProbe.setAttribute("aria-hidden", "true");
    document.body.appendChild(colorProbe);
  }
  colorProbe.style.color = value;
  return getComputedStyle(colorProbe).color;
}
