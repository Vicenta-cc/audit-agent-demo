import { expect, test } from "@playwright/test";

test("delete confirmation follows the blue workspace visual system", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/api/**", route => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load("/node_modules/.vite/deps/react.js")).default;
    const { createRoot } = (await load("/node_modules/.vite/deps/react-dom_client.js")).default;
    const { DeleteSessionDialog } = await load(
      "/src/features/investigation/DeleteSessionDialog.tsx"
    );
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    createRoot(host).render(React.createElement(DeleteSessionDialog, {
      title: "抖音新品用户反馈调查",
      busy: false,
      error: "",
      onCancel: () => {},
      onConfirm: () => {}
    }));
  });

  const dialog = page.getByRole("dialog", { name: "删除会话及报告" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("抖音新品用户反馈调查", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "取消" })).toHaveClass(/mt-button-secondary/);
  await expect(dialog.getByRole("button", { name: "永久删除" })).toHaveClass(/mt-button-danger/);
  const headerBackground = await dialog.locator(".inv-delete-dialog-header")
    .evaluate(element => getComputedStyle(element).backgroundImage);
  expect(headerBackground).toContain("linear-gradient");
  const iconColor = await dialog.locator(".inv-delete-dialog-icon")
    .evaluate(element => getComputedStyle(element).color);
  expect(iconColor).toBe("rgb(37, 99, 235)");
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/xhs-delete-session-dialog-20260921.png", fullPage: true });
});
