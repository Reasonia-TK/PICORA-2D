// 複数の独立した線・円弧を閉じて、ドメインの作成・保存・メッシュまで実UIで確認する。
import { mkdirSync, readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, menu, openApp, selectNode, setNumber, test } from "./fixtures";

async function point(page: Page, text: string) {
  const canvas = page.getByRole("application");
  await canvas.focus();
  await canvas.press(text[0]);
  const input = page.getByRole("textbox", { name: "座標の入力", exact: true });
  await input.fill(text);
  await input.press("Enter");
}

async function blankAxisymmetric(page: Page) {
  await openApp(page);
  await menu(page, "ファイル", "新規");
  await selectNode(page, "パーツ");
  await expect(page.getByLabel("作成モード", { exact: true })).toHaveValue("sketch");
}

async function save(page: Page) {
  const download = page.waitForEvent("download");
  await menu(page, "ファイル", "名前を付けて保存…");
  const path = await (await download).path();
  return { path, project: JSON.parse(readFileSync(path, "utf8")) };
}

for (const [label, theme] of [["ライト", "light"], ["ダーク", "dark"]] as const) {
  test(`closes independent sketch lines into an axisymmetric domain in ${theme}`, async ({ page }) => {
    await blankAxisymmetric(page);
    await page.getByRole("menuitem", { name: "表示", exact: true }).click();
    await page.getByRole("menuitem", { name: /^テーマ/ }).click();
    await page.getByRole("menuitemradio", { name: label, exact: true }).click();
    await page.getByRole("button", { name: "スケッチからドメイン作成", exact: true }).click();
    // 4本の線を別々に確定。最後の線で閉鎖し、手動の昇格操作なしでパーツになる。
    await page.getByRole("button", { name: "線 (スケッチ)", exact: true }).click();
    for (const [a, b] of [["20,10", "40,10"], ["40,30", "40,10"], ["20,30", "40,30"]]) {
      await point(page, a);
      await point(page, b);
      await page.getByRole("application").press("Enter");
    }
    await expect(page.locator(".tree")).not.toContainText("region1");
    mkdirSync("test-results/qa", { recursive: true });
    await page.screenshot({ path: `test-results/qa/picora-2d-sketch-edit-${theme}.png`, fullPage: true });
    await point(page, "20,10");
    await point(page, "20,30");
    await expect(page.locator(".tree")).toContainText("region1");
    await page.getByRole("application").press("Escape");
    await page.getByRole("application").press("Escape");
    await page.keyboard.press("Control+z");
    await expect(page.locator(".tree")).not.toContainText("region1");
    await selectNode(page, "スケッチ");
    await expect(page.getByText("スケッチ 3 個", { exact: true })).toBeVisible();
    await page.keyboard.press("Control+y");
    await selectNode(page, "region1");
    await page.getByLabel("種類", { exact: true }).selectOption("dielectric");
    await setNumber(page, "比誘電率 εr", "4");
    mkdirSync("test-results/qa", { recursive: true });
    await page.screenshot({ path: `test-results/qa/picora-2d-sketch-domain-${theme}.png`, fullPage: true });
    const saved = await save(page);
    expect(saved.project.coord).toBe("rz_x0");
    expect(saved.project.geometry.regions).toHaveLength(1);
    expect(saved.project.geometry.regions[0]).toMatchObject({ type: "dielectric", eps_r: 4 });
    expect(saved.project.geometry.regions[0].polygon).toHaveLength(4);
    expect(saved.project.cad.sketch).toHaveLength(0);
    expect(saved.project.geometry.domain.polygon).toEqual([[0.015, 0.005], [0.045, 0.005], [0.045, 0.035], [0.015, 0.035]]);
    const chooser = page.waitForEvent("filechooser");
    await menu(page, "ファイル", "開く…");
    await (await chooser).setFiles(saved.path);
    await selectNode(page, "メッシュ");
    await page.getByRole("button", { name: "メッシュを作成" }).click();
    await expect(page.getByText(/節点 \d+・要素 \d+/).first()).toBeVisible();
  });
}

test("closes a straight line and a three-point arc into a curved domain", async ({ page }) => {
  await blankAxisymmetric(page);
  await page.getByRole("button", { name: "線 (スケッチ)", exact: true }).click();
  await point(page, "20,10");
  await point(page, "40,10");
  await page.getByRole("application").press("Enter");
  await expect(page.locator(".tree")).not.toContainText("region1");
  await page.getByRole("button", { name: "3 点の円弧 (スケッチ)", exact: true }).click();
  await point(page, "40,10");
  await point(page, "30,20");
  await point(page, "20,10");
  await expect(page.locator(".tree")).toContainText("region1");
  const saved = await save(page);
  expect(saved.project.geometry.regions[0].polygon).toHaveLength(2);
  expect(Math.max(...saved.project.geometry.regions[0].bulges)).toBeCloseTo(1, 12);
  expect(saved.project.cad.sketch).toHaveLength(0);
});

test("keeps a closed construction sketch and promotes it when its purpose changes", async ({ page }) => {
  await blankAxisymmetric(page);
  await page.getByLabel("作成モード", { exact: true }).selectOption("construction");
  await page.getByRole("button", { name: "折れ線", exact: true }).click();
  for (const q of ["20,10", "40,10", "30,30", "20,10"]) await point(page, q);
  await expect(page.locator(".tree")).not.toContainText("region1");
  await expect(page.getByRole("checkbox", { name: "補助スケッチとして残す", exact: true })).toBeChecked();
  const pending = await save(page);
  expect(pending.project.cad.sketch[0]).toMatchObject({ construction: true, closed: true });
  await page.getByText("補助スケッチとして残す", { exact: true }).click();
  await expect(page.locator(".tree")).toContainText("region1");
  const promoted = await save(page);
  expect(promoted.project.geometry.regions[0].polygon).toHaveLength(3);
  expect(promoted.project.cad.sketch).toHaveLength(0);
});
