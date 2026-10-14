// 共通 CAD の抽出後の確認。
import { expect, openBlankApp, selectNode, test } from "./fixtures";

test("creates an internal circle and builds its embedded boundary mesh", async ({ page }) => {
  await openBlankApp(page);
  await selectNode(page, "計算領域");
  await expect(page.getByText("頂点と円弧", { exact: true })).toHaveCount(0);
  await selectNode(page, "パーツ");
  await page.getByRole("button", { name: "円の領域を追加", exact: true }).click();
  await expect(page.locator(".tree")).toContainText("region1");
  await selectNode(page, "メッシュ");
  await page.getByRole("button", { name: "メッシュを作成" }).click();
  await expect(page.getByText(/節点 \d+・要素 \d+/).first()).toBeVisible();
});

test("draws a rectangle on the canvas and undoes it", async ({ page }) => {
  await openBlankApp(page);
  await page.getByRole("button", { name: "矩形", exact: true }).click();
  const canvas = page.getByRole("application");
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.35);
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await expect(page.locator(".tree")).toContainText("region1");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect(page.locator(".tree")).not.toContainText("region1");
});
