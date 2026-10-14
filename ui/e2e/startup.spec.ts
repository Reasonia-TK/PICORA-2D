// 起動時はGEC Cell、新規の空文書も(r,z)。保存済み文書の座標系は維持する。
import { mkdirSync, readFileSync } from "node:fs";
import { expect, menu, openApp, selectNode, test } from "./fixtures";

const gec = JSON.parse(readFileSync("../examples/gec_cell.json", "utf8"));

for (const [label, mode] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
  test(`starts with the GEC cell in (r,z), then creates an axisymmetric blank document in ${mode}`, async ({ page }) => {
    await openApp(page);
    await expect(page.locator(".tree")).toContainText("GEC 基準セル");
    await expect(page.locator(".tree")).toContainText("rf_electrode");
    await expect(page.locator(".tree")).toContainText("ground_electrode");
    await selectNode(page, "計算領域");
    await expect(page.getByLabel("座標系", { exact: true })).toHaveValue("rz_x0");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("manual");
    await page.getByRole("menuitem", { name: "表示", exact: true }).click();
    await page.getByRole("menuitem", { name: /^テーマ/ }).click();
    await page.getByRole("menuitemradio", { name: label, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    mkdirSync("test-results/qa", { recursive: true });
    await page.screenshot({ path: `test-results/qa/picora-2d-startup-gec-${mode}.png`, fullPage: true });
    const download = page.waitForEvent("download");
    await menu(page, "ファイル", "名前を付けて保存…");
    const saved = JSON.parse(readFileSync(await (await download).path(), "utf8"));
    expect(saved.coord).toBe("rz_x0");
    expect(saved.geometry.domain.polygon).toEqual(gec.geometry.domain.polygon);
    expect(saved.geometry.regions).toEqual(gec.geometry.regions);
    expect(saved.geometry.boundaries).toEqual(gec.geometry.boundaries);
    expect(saved.mesh.mode).toBe("cartesian");
    expect(saved.results).toBeUndefined();
    await menu(page, "ファイル", "新規");
    await selectNode(page, "計算領域");
    await expect(page.getByLabel("座標系", { exact: true })).toHaveValue("rz_x0");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("auto");
    await expect(page.locator(".tree").getByText("rf_electrode", { exact: true })).toHaveCount(0);
  });
}
