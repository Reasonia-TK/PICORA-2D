// 実Canvasの描画を観測し、文字と縁取りの色が同じになって潰れる再発を防ぐ。
// 保存済みGEC結果を使い、ジオメトリ・場のカラーバー・DPIを両テーマで確認する。
import { mkdirSync, writeFileSync } from "node:fs";
import { expect, openApp, selectNode, test } from "./fixtures";

for (const dpr of [1, 1.5]) {
  test.describe(`canvas text at DPR ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr });
    for (const [label, theme] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
      test(`keeps labels legible on geometry and saved fields in ${theme}`, async ({ page }) => {
        await page.addInitScript(() => {
          type Label = { text: string; fill: string; stroke: string; width: number; font: string };
          const records: Label[] = [];
          Object.assign(window, { overlayTextSamples: records });
          const proto = CanvasRenderingContext2D.prototype;
          const clear = proto.clearRect;
          const stroke = proto.strokeText;
          const fill = proto.fillText;
          const last = new WeakMap<CanvasRenderingContext2D, string>();
          proto.clearRect = function (...args) {
            if (this.canvas.classList.contains("viewer-top")) records.length = 0;
            return clear.apply(this, args);
          };
          proto.strokeText = function (text, x, y, maxWidth) {
            last.set(this, text);
            return maxWidth === undefined ? stroke.call(this, text, x, y) : stroke.call(this, text, x, y, maxWidth);
          };
          proto.fillText = function (text, x, y, maxWidth) {
            if (this.canvas.classList.contains("viewer-top") && last.get(this) === text) {
              records.push({ text, fill: String(this.fillStyle), stroke: String(this.strokeStyle), width: this.lineWidth, font: this.font });
              last.delete(this);
            }
            return maxWidth === undefined ? fill.call(this, text, x, y) : fill.call(this, text, x, y, maxWidth);
          };
        });
        await openApp(page);
        await page.getByRole("menuitem", { name: "表示", exact: true }).click();
        await page.getByRole("menuitem", { name: /^テーマ/ }).click();
        await page.getByRole("menuitemradio", { name: label, exact: true }).click();
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await selectNode(page, "ground_electrode");
        const samples = () => page.evaluate(() => (window as unknown as { overlayTextSamples: { text: string; fill: string; stroke: string; width: number; font: string }[] }).overlayTextSamples);
        await expect.poll(async () => (await samples()).filter((s) => s.text.includes("electrode")).length).toBeGreaterThanOrEqual(2);
        mkdirSync("test-results/qa", { recursive: true });
        const prefix = `test-results/qa/picora-2d-text-${theme}-dpr${dpr}`;
        const geometry = await samples();
        await page.screenshot({ path: `${prefix}-geometry.png`, fullPage: true });
        await page.getByRole("menuitem", { name: "ファイル", exact: true }).click();
        await page.getByRole("menuitem", { name: /^サンプル/ }).click();
        await page.getByRole("menuitem", { name: "GEC 基準セル (流体 2D・PIC、軸対称)", exact: true }).click();
        const result = page.locator(".tree").getByText(/^流体.*GEC.*保存された結果/);
        await expect(result).toBeVisible();
        await result.click();
        await expect.poll(async () => (await samples()).filter((s) => /^[-+]?\d/.test(s.text)).length).toBeGreaterThanOrEqual(4);
        const field = await samples();
        await page.screenshot({ path: `${prefix}-field.png`, fullPage: true });
        await page.getByRole("button", { name: "表示", exact: true }).click();
        await page.locator("#viewer-quantity").selectOption("n_e");
        await page.keyboard.press("Escape");
        await expect.poll(async () => (await samples()).filter((s) => /e\+\d+/.test(s.text)).length).toBeGreaterThanOrEqual(4);
        const density = await samples();
        await page.screenshot({ path: `${prefix}-density.png`, fullPage: true });
        writeFileSync(`${prefix}.json`, JSON.stringify({ dpr, theme, geometry, field, density }, null, 2));
        for (const sample of [...geometry, ...field, ...density]) expect(sample.stroke, sample.text).not.toBe(sample.fill);
        // 同じ大きさのCanvasは再利用される。選択や再描画でも文字を重ねて太らせない。
        await selectNode(page, "rf_electrode");
        for (const sample of await samples()) expect(sample.stroke, sample.text).not.toBe(sample.fill);
      });
    }
  });
}
