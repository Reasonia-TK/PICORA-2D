// パーツ先行・計算領域追従の保存/履歴/GPU向けメッシュを実UIから確認。
import { mkdirSync, readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, menu, openBlankApp, selectNode, setNumber, test } from "./fixtures";

async function saveDocument(page: Page) {
  const download = page.waitForEvent("download");
  await menu(page, "ファイル", "名前を付けて保存…");
  const file = await (await download).path();
  return { file, json: JSON.parse(readFileSync(file, "utf8")) };
}

for (const [themeLabel, mode] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
  test(`parts-first automatic domain, manual override, history and save/load in ${mode}`, async ({ page }) => {
    await openBlankApp(page);
    await expect(page.getByRole("heading", { name: "パーツ", exact: true })).toBeVisible();
    await page.getByRole("menuitem", { name: "表示", exact: true }).click();
    await page.getByRole("menuitem", { name: /^テーマ/ }).click();
    await page.getByRole("menuitemradio", { name: themeLabel, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    await page.getByRole("button", { name: "円の領域を追加", exact: true }).click();
    // 初期の100×50mm外周から離れた場所へパーツを配置。
    await setNumber(page, "x", "250");
    await setNumber(page, "y", "-80");
    await setNumber(page, "半径", "20");
    await selectNode(page, "計算領域");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("auto");
    await setNumber(page, "パーツからの余白", "8");
    await expect(page.getByTestId("domain-bounds")).toContainText("222 mm");
    await expect(page.getByTestId("domain-bounds")).toContainText("-108 mm");
    await expect(page.getByTestId("domain-bounds")).toContainText("56 mm");
    mkdirSync("test-results/qa", { recursive: true });
    await page.screenshot({ path: `test-results/qa/picora-2d-geometry-${mode}.png`, fullPage: true });

    await page.getByLabel("計算領域の追従", { exact: true }).selectOption("manual");
    await expect(page.getByLabel("原点 X", { exact: true })).toHaveValue("222");
    await selectNode(page, "region1");
    await setNumber(page, "移動 x", "300");
    await setNumber(page, "移動 y", "0");
    await page.locator(".transform-panel").getByRole("button", { name: "適用", exact: true }).first().click();
    await selectNode(page, "計算領域");
    await expect(page.getByLabel("原点 X", { exact: true })).toHaveValue("222");
    await page.getByLabel("計算領域の追従", { exact: true }).selectOption("auto");
    await expect(page.getByTestId("domain-bounds")).toContainText("522 mm");
    await page.keyboard.press("Control+z");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("manual");
    await page.keyboard.press("Control+y");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("auto");
    const saved = await saveDocument(page);
    expect(saved.json.geometry.computational_domain).toEqual({ mode: "auto", margin: 0.008 });
    expect(saved.json.geometry.domain.polygon).toEqual([[0.522, -0.108], [0.578, -0.108], [0.578, -0.052], [0.522, -0.052]]);
    expect(saved.json.geometry.domain.edge_ids).toEqual(["e1", "e2", "e3", "e4"]);
    expect(saved.json.geometry.boundaries.map((b: { edges: number[] }) => b.edges)).toEqual([[1]]);
    const chooser = page.waitForEvent("filechooser");
    await menu(page, "ファイル", "開く…");
    await (await chooser).setFiles(saved.file);
    await selectNode(page, "計算領域");
    await expect(page.getByLabel("計算領域の追従", { exact: true })).toHaveValue("auto");
    await expect(page.getByTestId("domain-bounds")).toContainText("522 mm");
    await selectNode(page, "メッシュ");
    await page.getByRole("button", { name: "メッシュを作成" }).click();
    await expect(page.getByText(/節点 \d+・要素 \d+/).first()).toBeVisible();
    await selectNode(page, "パーツ");
    await page.getByRole("button", { name: "削除", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除", exact: true }).click();
    await selectNode(page, "計算領域");
    await expect(page.getByTestId("domain-bounds")).toContainText("100 mm");
    await page.keyboard.press("Control+z");
    await expect(page.getByTestId("domain-bounds")).toContainText("522 mm");
  });
}

test("axisymmetric tracking clips negative radius without moving a circle", async ({ page }) => {
  await openBlankApp(page);
  await page.getByRole("button", { name: "円の領域を追加", exact: true }).click();
  await setNumber(page, "x", "0");
  await setNumber(page, "y", "0");
  await setNumber(page, "半径", "20");
  await selectNode(page, "計算領域");
  await page.getByLabel("座標系", { exact: true }).selectOption("rz_x0");
  await expect(page.getByText(/径方向 r < 0 の部分を計算から除外/).first()).toBeVisible();
  const saved = await saveDocument(page);
  expect(saved.json.geometry.regions[0].shape.center).toEqual([0, 0]);
  expect(saved.json.geometry.domain.polygon).toEqual([[0, -0.025], [0.025, -0.025], [0.025, 0.025], [0, 0.025]]);
  expect(saved.json.geometry.boundaries.every((bc: { edges: number[] }) => !bc.edges.includes(3))).toBe(true);
  await selectNode(page, "メッシュ");
  await page.getByRole("button", { name: "メッシュを作成" }).click();
  await expect(page.getByText(/節点 \d+・要素 \d+/).first()).toBeVisible();
});
