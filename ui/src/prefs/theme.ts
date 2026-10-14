import { useEffect, useState } from "react";
import { usePrefs } from "./prefs";

/** 設定はsystemのまま保存し、描画側にはOSに追従する解決済みモードを渡す。 */
export function useResolvedTheme(): "light" | "dark" {
  const preference = usePrefs((s) => s.theme);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const changed = () => setSystemDark(media.matches);
    media.addEventListener("change", changed);
    changed();
    return () => media.removeEventListener("change", changed);
  }, []);
  return preference === "system" ? (systemDark ? "dark" : "light") : preference;
}
