// 2D 専用 UI: 接続・スタディ制限・直交格子・流体実行・結果保存。
import { mkdirSync, readFileSync } from "node:fs";
import { enableStudy, expect, menu, openApp, openBlankApp, selectNode, setNumber, test } from "./fixtures";
import { runFluid2d } from "./flows";

test("opens the 2D plasma sample and connects to the backend", async ({ page }) => {
  await openApp(page);
  await expect(page).toHaveTitle(/PICORA-2D/);
  await expect(page.locator(".tree")).toContainText("GEC 基準セル");
  await expect(page.locator(".tree")).toContainText("PIC-MCC (2D)");
  await expect(page.locator(".tree")).toContainText("流体 (2D)");
  for (const name of ["PIC-MCC (1D)", "流体プラズマ (1D)", "静電場", "DSMC", "粒子軌道", "定在波"]) {
    await expect(page.locator(".tree").getByText(name, { exact: true })).toHaveCount(0);
  }
  await selectNode(page, "計算領域");
  await setNumber(page, "半径 R (r)", "120");
  await expect(page).toHaveTitle(/^● /);
  await page.keyboard.press("Control+z");
  await expect(page).not.toHaveTitle(/^● /);
});

test("builds the Cartesian mesh with embedded boundaries", async ({ page }) => {
  await openBlankApp(page);
  await selectNode(page, "メッシュ");
  await expect(page.getByText("GPU 計算向けの直交格子 + 埋込境界を使用します。")).toBeVisible();
  await expect(page.getByLabel("方式", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "メッシュを作成" }).click();
  await expect(page.getByText(/節点 \d+・要素 \d+/).first()).toBeVisible();
});

test("runs 2D fluid plasma and saves and reloads the result", async ({ page }) => {
  await openBlankApp(page);
  await runFluid2d(page);
  const download = page.waitForEvent("download");
  await menu(page, "ファイル", "結果付きで保存…");
  const file = await (await download).path();
  const saved = JSON.parse(readFileSync(file, "utf8"));
  expect(saved.mesh.mode).toBe("cartesian");
  expect(saved.results.fluid2d).toBeTruthy();
  expect(saved.results.pic1d).toBeUndefined();
  const chooser = page.waitForEvent("filechooser");
  await menu(page, "ファイル", "開く…");
  const discard = page.getByRole("button", { name: "保存しない", exact: true });
  if (await discard.isVisible()) await discard.click();
  await (await chooser).setFiles(file);
  await expect(page.locator(".tree")).toContainText("流体 2D (");
});

test("opens the saved PIC sample result", async ({ page }) => {
  await openApp(page);
  await page.getByRole("menuitem", { name: "ファイル", exact: true }).click();
  await page.getByRole("menuitem", { name: /^サンプル/ }).click();
  await page.getByRole("menuitem", { name: "容量結合プラズマ (PIC)", exact: true }).click();
  const run = page.locator(".tree").getByText(/^PIC \(容量結合プラズマ \(PIC\)、保存された結果/);
  await expect(run).toBeVisible({ timeout: 30_000 });
  await run.click();
  await expect(page.getByText("時間平均したステップ数")).toBeVisible();
});

test("captures warm clay light and dark themes", async ({ page }) => {
  await openApp(page);
  mkdirSync("test-results/qa", { recursive: true });
  for (const [label, mode] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
    await page.getByRole("menuitem", { name: "表示", exact: true }).click();
    await page.getByRole("menuitem", { name: /^テーマ/ }).click();
    await page.getByRole("menuitemradio", { name: label, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    await expect.poll(() => page.locator(".viewer-canvas canvas").first().evaluate((canvas: HTMLCanvasElement) => [...canvas.getContext("2d")!.getImageData(100, 100, 1, 1).data].slice(0, 3))).toEqual(mode === "light" ? [250, 249, 245] : [38, 38, 36]);
    await page.screenshot({ path: `test-results/qa/picora-2d-${mode}.png`, fullPage: true });
  }
});

test("registers a named CSV set and applies it to both 2D solvers", async ({ page }) => {
  await openApp(page);
  await selectNode(page, "PIC-MCC (2D)");
  await enableStudy(page);
  await selectNode(page, "流体 (2D)");
  await enableStudy(page);
  await selectNode(page, "衝突断面積");
  const setName = `E2E 合成検査セット ${Date.now()}`;
  await page.getByLabel("セット名", { exact: true }).fill(setName);
  await page.getByLabel("形式", { exact: true }).selectOption("csv");
  await page.getByLabel("断面積テキスト", { exact: true }).fill("energy[eV],sigma[m2]\n0,0\n1,2e-20\n5,3e-20\n20,2e-20\n100,1e-20");
  await page.getByLabel("過程名（任意）", { exact: true }).fill("E2E elastic");
  await page.getByLabel("出典", { exact: true }).fill("PICORA E2E synthetic validation");
  await page.getByRole("button", { name: "セットを登録", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("1 過程を登録しました");
  await expect(page.getByRole("radio", { name: new RegExp(setName) })).toBeChecked();
  await page.getByRole("button", { name: "2D PIC MCC へセット適用", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("2D PIC MCCへ適用しました");
  await page.getByRole("button", { name: "2D 流体 電子係数へセット適用", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("2D 流体へ適用しました");
  const download = page.waitForEvent("download");
  await menu(page, "ファイル", "名前を付けて保存…");
  const saved = JSON.parse(readFileSync(await (await download).path(), "utf8"));
  expect(saved.pic.mcc.gas.name).toBe("Ar");
  expect(saved.pic.mcc.electron_processes).toHaveLength(1);
  expect(saved.pic.mcc.ion_processes).toHaveLength(0);
  expect(saved.pic.mcc.xs_registry_set.name).toBe(setName);
  expect(saved.pic.mcc.electron_xs_sources[0].source).toBe("PICORA E2E synthetic validation");
  expect(saved.fluid2d.electron_processes).toHaveLength(1);
  expect(saved.fluid2d.electron_model).toBe("maxwell");
  expect(saved.fluid2d.ion_mass_amu).toBeCloseTo(39.948);
  expect(saved.fluid2d.xs_registry_set.name).toBe(setName);
  expect(saved.fluid2d.xs_registry_sources[0].set_name).toBe(setName);
  mkdirSync("test-results/qa", { recursive: true });
  for (const [label, mode] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
    await page.getByRole("menuitem", { name: "表示", exact: true }).click();
    await page.getByRole("menuitem", { name: /^テーマ/ }).click();
    await page.getByRole("menuitemradio", { name: label, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    await page.getByLabel("登録先", { exact: true }).selectOption("new");
    await page.locator(".xs-registry > h2").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/qa/picora-2d-xs-register-${mode}.png`, fullPage: true });
    await page.locator(".xs-set-list").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/qa/picora-2d-xs-${mode}.png`, fullPage: true });
  }
  await page.getByRole("button", { name: "選択セットを削除", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("を削除しました");
});
