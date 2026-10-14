// CSV 複数ファイル、セットの名前・内訳編集、既存過程のコピー、MCC の全種別適用。
import { readFileSync } from "node:fs";
import { enableStudy, expect, menu, openApp, selectNode, test } from "./fixtures";

const CSV = "energy[eV],sigma[m2]\n0,0\n10,1e-20\n100,2e-20\n";

test("imports multiple CSV processes and edits and applies named sets", async ({ page }) => {
  await openApp(page);
  await selectNode(page, "PIC-MCC (2D)");
  await enableStudy(page);
  await selectNode(page, "衝突断面積");
  const name = `E2E 複数CSV合成 ${Date.now()}`;
  const renamed = `${name} 編集済み`;
  const copied = `${name} コピー先`;
  await page.getByLabel("セット名", { exact: true }).fill(name);
  await page.getByLabel("形式", { exact: true }).selectOption("csv");
  await page.getByLabel("出典", { exact: true }).fill("合成 E2E データ。物理計算用ではありません");
  await page.getByLabel("CSV ファイル（複数可）", { exact: true }).setInputFiles([
    { name: "synthetic-elastic.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) },
    { name: "synthetic-ion.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) },
  ]);
  await page.getByLabel("synthetic-ion.csv 衝突種別", { exact: true }).selectOption("ISOTROPIC");
  await page.getByLabel("synthetic-ion.csv 入射粒子", { exact: true }).fill("Ar^+");
  await page.getByRole("button", { name: "セットを登録", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("合計 2 過程");
  await expect(page.locator(".xs-set-details tbody tr")).toHaveCount(2);
  await page.getByLabel("選択セット名", { exact: true }).fill(renamed);
  await page.getByRole("button", { name: "名前を更新", exact: true }).click();
  await expect(page.getByRole("radio", { name: new RegExp(renamed) })).toBeChecked();
  await page.getByLabel("synthetic-elastic を編集対象にする", { exact: true }).check();
  await page.getByText("既存過程を別セットへ追加・内訳を編集", { exact: true }).click();
  await page.getByLabel("コピー先セット名", { exact: true }).fill(copied);
  await page.getByRole("button", { name: "選択過程を追加 (1)", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("合計 1 過程");
  await page.getByRole("radio", { name: new RegExp(renamed) }).check();
  await page.getByLabel("synthetic-ion を編集対象にする", { exact: true }).check();
  const editor = page.locator(".xs-process-edit");
  if (!(await editor.evaluate((element: HTMLDetailsElement) => element.open))) await editor.locator("summary").click();
  await page.getByLabel("過程の追加先", { exact: true }).selectOption({ label: copied });
  await page.getByRole("button", { name: "選択過程を追加 (1)", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("合計 2 過程");
  await page.getByRole("button", { name: "2D PIC MCC へセット適用", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("電子 1 過程、イオン 1 過程");
  const download = page.waitForEvent("download");
  await menu(page, "ファイル", "名前を付けて保存…");
  const saved = JSON.parse(readFileSync(await (await download).path(), "utf8"));
  expect(saved.pic.mcc.electron_processes).toHaveLength(1);
  expect(saved.pic.mcc.ion_processes).toHaveLength(1);
  expect(saved.pic.mcc.xs_registry_set.name).toBe(copied);
  expect(saved.pic.mcc.ion_xs_sources[0].source).toContain("合成 E2E");
  await page.getByLabel("synthetic-ion を編集対象にする", { exact: true }).check();
  const currentEditor = page.locator(".xs-process-edit");
  if (!(await currentEditor.evaluate((element: HTMLDetailsElement) => element.open))) await currentEditor.locator("summary").click();
  await page.getByRole("button", { name: "選択過程をセットから除外", exact: true }).click();
  await expect(page.locator(".xs-set-details tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "選択セットを削除", exact: true }).click();
  await expect(page.getByRole("radio", { name: new RegExp(copied) })).toHaveCount(0);
  await page.getByRole("radio", { name: new RegExp(renamed) }).check();
  await page.getByRole("button", { name: "選択セットを削除", exact: true }).click();
  await expect(page.getByRole("radio", { name: new RegExp(renamed) })).toHaveCount(0);
});
