import { chromium, expect, test, type Locator } from "@playwright/test";
import { join } from "node:path";

const baseUrl = process.env.R31_VISUAL_BASE_URL || "";
const executablePath = process.env.R31_CHROMIUM_EXECUTABLE || "";
const screenshotPath = process.env.R31_PRINT_SCREENSHOT || "r31-report-print.png";
const screenshotDir = process.env.R31_VISUAL_DIR || ".";
const reportUrl = `${baseUrl}/investigation/r31-real-b/report`
  + "?report=report-version%3A4e3ebeccd2ed4f0c9c9a750332d22585"
  + "&section=section-2-3";

async function readAccountTableGeometry(table: Locator) {
  return table.evaluate((element) => {
    const header = element.querySelector<HTMLElement>(".r31-account-table-head")!;
    const row = element.querySelector<HTMLElement>(".r31-account-table-row")!;
    const accountName = row.querySelector<HTMLElement>(".r31-account-table-name strong")!;
    const headerCells = Array.from(header.children) as HTMLElement[];
    const rowCells = Array.from(row.children) as HTMLElement[];
    return {
      tracks: getComputedStyle(header).gridTemplateColumns.split(" ").map((track) => parseFloat(track)),
      width: header.getBoundingClientRect().width,
      headerHeight: header.getBoundingClientRect().height,
      rowHeight: row.getBoundingClientRect().height,
      headerFontSize: parseFloat(getComputedStyle(header).fontSize),
      rowFontSize: parseFloat(getComputedStyle(row).fontSize),
      accountNameFontSize: parseFloat(getComputedStyle(accountName).fontSize),
      headerAlignments: headerCells.map((cell) => getComputedStyle(cell).textAlign),
      rowAlignments: rowCells.map((cell) => getComputedStyle(cell).textAlign),
      accountNameJustification: getComputedStyle(rowCells[0]).justifyContent,
      columnCenterDeltas: headerCells.map((cell, index) => {
        const headerBox = cell.getBoundingClientRect();
        const rowBox = rowCells[index].getBoundingClientRect();
        return Math.abs(
          headerBox.x + headerBox.width / 2 - (rowBox.x + rowBox.width / 2)
        );
      }),
      headerCellsFit: headerCells.every((cell) => cell.scrollWidth <= cell.clientWidth)
    };
  });
}

function expectBalancedAccountColumns(
  geometry: Awaited<ReturnType<typeof readAccountTableGeometry>>,
  numericColumnCount: number
) {
  const numericTracks = geometry.tracks.slice(1, numericColumnCount + 1);
  expect(geometry.tracks[0] / geometry.width).toBeLessThan(0.3);
  expect(Math.max(...numericTracks) - Math.min(...numericTracks)).toBeLessThanOrEqual(1);
  expect(geometry.headerFontSize).toBe(12);
  expect(geometry.rowFontSize).toBe(13.5);
  expect(geometry.accountNameFontSize).toBe(13.5);
  expect(geometry.headerAlignments.every((alignment) => alignment === "center")).toBe(true);
  expect(geometry.rowAlignments[0]).toBe("left");
  expect(geometry.rowAlignments.slice(1).every((alignment) => alignment === "center")).toBe(true);
  expect(geometry.accountNameJustification).toBe("flex-start");
  expect(Math.max(...geometry.columnCenterDeltas)).toBeLessThanOrEqual(0.5);
  expect(geometry.headerHeight).toBe(geometry.rowHeight);
}

test("published R3.1 account overview keeps server tables and unified Drawer state", async () => {
  test.skip(!baseUrl || !executablePath, "real visual acceptance environment is not configured");
  test.setTimeout(120_000);

  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    await page.goto(reportUrl, { waitUntil: "networkidle" });

    const section = page.locator("#report-section-2-3");
    const toolbar = page.locator(".ethnic-report-toolbar");
    const targetHeading = section.getByRole("heading", { name: "调查目标", exact: true });
    const publishingGroups = section.locator(".r31-publishing-account-groups");
    const targetRow = publishingGroups.locator(".r31-account-table-row").first();
    const crossGroup = section.locator(".r31-dynamic-commenters");
    const riskGroup = section.locator(".r31-risk-commenters");
    const crossRows = crossGroup.locator(".r31-account-table-row");
    const riskRows = riskGroup.locator(".r31-account-table-row");

    await expect(targetRow).toContainText("麦热依姆古丽");
    await expect(section.locator(".r31-account-coverage .r31-stat-item")).toHaveCount(4);
    const firstCoverageItem = section.locator(".r31-account-coverage .r31-stat-item").first();
    const firstCoverageValueBox = await firstCoverageItem.locator("dd").boundingBox();
    const firstCoverageLabelBox = await firstCoverageItem.locator("dt").boundingBox();
    expect(firstCoverageValueBox).not.toBeNull();
    expect(firstCoverageLabelBox).not.toBeNull();
    expect(firstCoverageLabelBox!.y).toBeGreaterThan(firstCoverageValueBox!.y);
    expect(await firstCoverageItem.locator("dt, dd").evaluateAll((elements) => (
      elements.every((element) => getComputedStyle(element).textAlign === "center")
    ))).toBe(true);
    const contentScaleBand = page.locator(".r31-stat-band");
    const firstFindingFacts = page.locator(".r31-finding-facts").first();
    await expect(contentScaleBand.locator(".r31-stat-item")).toHaveCount(2);
    await expect(firstFindingFacts.locator(".r31-stat-item")).toHaveCount(3);
    for (const value of ["421", "0", "303", "12", "285"]) {
      await expect(targetRow.getByText(value, { exact: true }).first()).toBeVisible();
    }
    await expect(publishingGroups.locator(".r31-account-table-head").first()).toHaveText(
      /账号评论风险评论发布风险帖子涉及帖子详情/
    );
    const accountGroupStyle = await publishingGroups.locator(".r31-account-group").first().evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        borderTopWidth: style.borderTopWidth,
        borderRadius: style.borderRadius
      };
    });
    expect(accountGroupStyle).toEqual({
      backgroundColor: "rgba(0, 0, 0, 0)",
      borderTopWidth: "0px",
      borderRadius: "0px"
    });
    expect(await publishingGroups.locator(".r31-account-table").first().evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        borderTopWidth: style.borderTopWidth,
        borderRightWidth: style.borderRightWidth,
        borderBottomWidth: style.borderBottomWidth,
        borderLeftWidth: style.borderLeftWidth,
        borderRadius: style.borderRadius
      };
    })).toEqual({
      borderTopWidth: "1px",
      borderRightWidth: "0px",
      borderBottomWidth: "1px",
      borderLeftWidth: "0px",
      borderRadius: "0px"
    });
    expect(await publishingGroups.locator(".r31-account-table-head").first().evaluate(
      (element) => getComputedStyle(element).backgroundColor
    )).toBe("rgba(0, 0, 0, 0)");
    expectBalancedAccountColumns(
      await readAccountTableGeometry(publishingGroups.locator(".r31-account-table").first()),
      5
    );
    expectBalancedAccountColumns(
      await readAccountTableGeometry(crossGroup.locator(".r31-account-table")),
      4
    );
    await expect(targetRow.locator(".r31-account-table-detail")).toHaveText("");
    await expect(targetRow.locator(".r31-account-table-detail")).toHaveAttribute(
      "title",
      "查看账号活动概览"
    );
    await expect(crossGroup.getByText("基于当前可访问调查", { exact: true })).toBeVisible();
    await expect(riskGroup.getByText("基于本次发布报告", { exact: true })).toBeVisible();
    await expect(crossRows).toHaveCount(5);
    await expect(riskRows).toHaveCount(5);
    for (const row of await riskRows.all()) {
      expect(Number(await row.locator("[role=cell]").nth(1).textContent())).toBeGreaterThan(0);
    }

    const sectionBox = await section.boundingBox();
    const toolbarBox = await toolbar.boundingBox();
    const headingBox = await targetHeading.boundingBox();
    const targetBox = await targetRow.boundingBox();
    const crossBox = await crossGroup.boundingBox();
    const riskBox = await riskGroup.boundingBox();
    expect(sectionBox).not.toBeNull();
    expect(toolbarBox).not.toBeNull();
    expect(headingBox).not.toBeNull();
    expect(targetBox).not.toBeNull();
    expect(crossBox).not.toBeNull();
    expect(riskBox).not.toBeNull();
    expect(sectionBox!.y).toBeGreaterThanOrEqual(toolbarBox!.height);
    expect(targetBox!.y - headingBox!.y - headingBox!.height).toBeLessThan(58);
    expect(riskBox!.y).toBeGreaterThan(crossBox!.y + crossBox!.height);

    await section.screenshot({
      path: join(screenshotDir, "r31-account-overview-desktop-playwright.png")
    });
    await section.locator(".r31-account-coverage").screenshot({
      path: join(screenshotDir, "r31-account-coverage-band-playwright.png")
    });
    await contentScaleBand.screenshot({
      path: join(screenshotDir, "r31-content-scale-band-playwright.png")
    });
    await firstFindingFacts.screenshot({
      path: join(screenshotDir, "r31-finding-facts-band-playwright.png")
    });
    await publishingGroups.screenshot({
      path: join(screenshotDir, "r31-account-publishing-groups-playwright.png")
    });
    await crossGroup.screenshot({
      path: join(screenshotDir, "r31-cross-commenter-group-playwright.png")
    });
    await riskGroup.screenshot({
      path: join(screenshotDir, "r31-risk-commenter-group-playwright.png")
    });

    await targetRow.scrollIntoViewIfNeeded();
    const beforeDrawer = await page.evaluate(() => ({
      scrollY: window.scrollY,
      paperWidth: document.querySelector(".r31-report-paper")!.getBoundingClientRect().width
    }));
    await targetRow.click();
    const directOverview = page.getByRole("dialog", { name: "账号活动概览" });
    await expect(directOverview).toBeVisible();
    for (const heading of ["本次调查", "全部已授权调查", "活跃情况", "相关调查分布", "主要评论对象"]) {
      await expect(directOverview.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }
    const overviewMatrices = directOverview.locator(".r31-account-metrics.is-drawer");
    await expect(overviewMatrices).toHaveCount(2);
    await expect(overviewMatrices.first().locator(":scope > span")).toHaveCount(6);
    expect(await overviewMatrices.first().evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        columns: style.gridTemplateColumns.split(" ").length,
        borderTopWidth: style.borderTopWidth,
        borderRadius: style.borderRadius
      };
    })).toEqual({ columns: 3, borderTopWidth: "1px", borderRadius: "6px" });
    await expect(directOverview.getByRole("table", { name: "主要评论对象" })).toBeVisible();
    for (const header of ["评论对象", "评论数", "涉及帖子数"]) {
      await expect(directOverview.getByRole("columnheader", { name: header, exact: true })).toBeVisible();
    }
    await expect(directOverview.getByText("在当前授权范围内，该账号仅出现于本次调查。")).toBeVisible();
    const afterDrawer = await page.evaluate(() => ({
      scrollY: window.scrollY,
      paperWidth: document.querySelector(".r31-report-paper")!.getBoundingClientRect().width
    }));
    expect(afterDrawer).toEqual(beforeDrawer);
    await directOverview.screenshot({
      path: join(screenshotDir, "r31-account-overview-five-sections-playwright.png")
    });
    await directOverview.getByRole("button", { name: "关闭" }).click();

    await crossGroup.getByRole("button", { name: /^查看全部跨调查评论账号（88）$/ }).click();
    const indexDialog = page.getByRole("dialog", { name: "评论账号索引" });
    const searchInput = indexDialog.getByRole("searchbox", { name: "搜索账号显示名" });
    const sortSelect = indexDialog.getByRole("combobox", { name: "账号排序方式" });
    await expect(indexDialog.getByRole("button", { name: "跨调查评论账号 88" })).toHaveClass(/is-active/);
    await expect(indexDialog.getByText("当前结果 88", { exact: true })).toBeVisible();
    await expect(indexDialog.locator(".r31-account-table-row")).toHaveCount(20);
    const indexTableGeometry = await readAccountTableGeometry(indexDialog.locator(".r31-account-table"));
    expectBalancedAccountColumns(indexTableGeometry, 4);
    expect(indexTableGeometry.headerCellsFit).toBe(true);
    const indexTypography = await indexDialog.evaluate((element) => ({
      segment: parseFloat(getComputedStyle(element.querySelector(".r31-account-segments button")!).fontSize),
      basis: parseFloat(getComputedStyle(element.querySelector(".r31-account-index-basis")!).fontSize),
      search: parseFloat(getComputedStyle(element.querySelector(".r31-account-search input")!).fontSize),
      sort: parseFloat(getComputedStyle(element.querySelector(".r31-account-sort select")!).fontSize),
      count: parseFloat(getComputedStyle(element.querySelector(".r31-index-count")!).fontSize),
      pagination: parseFloat(getComputedStyle(element.querySelector(".r31-account-pagination")!).fontSize),
      paginationButton: parseFloat(getComputedStyle(element.querySelector(".r31-account-pagination button")!).fontSize)
    }));
    expect(indexTypography).toEqual({
      segment: 12,
      basis: 12,
      search: 12,
      sort: 12,
      count: 12,
      pagination: 12,
      paginationButton: 12
    });
    const paginationButtons = indexDialog.locator(".r31-account-pagination button");
    const previousPageBox = await paginationButtons.first().boundingBox();
    const nextPageBox = await paginationButtons.last().boundingBox();
    expect(previousPageBox).not.toBeNull();
    expect(nextPageBox).not.toBeNull();
    expect(previousPageBox!.width).toBe(nextPageBox!.width);
    expect(previousPageBox!.height).toBe(nextPageBox!.height);
    await page.screenshot({
      path: join(screenshotDir, "r31-comment-index-cross-playwright.png")
    });

    await searchInput.fill("100siz");
    await expect(indexDialog.getByText("当前结果 1", { exact: true })).toBeVisible();
    await sortSelect.selectOption("comment_count");
    await indexDialog.locator(".r31-account-table-row").first().click();
    const searchedOverview = page.getByRole("dialog", { name: "账号活动概览" });
    await expect(searchedOverview.getByRole("button", { name: "返回评论账号索引" })).toBeVisible();
    await searchedOverview.getByRole("button", { name: "返回评论账号索引" }).click();
    await expect(searchInput).toHaveValue("100siz");
    await expect(sortSelect).toHaveValue("comment_count");

    await searchInput.fill("");
    await expect(indexDialog.getByText("当前结果 88", { exact: true })).toBeVisible();
    await indexDialog.getByRole("button", { name: "下一页" }).click();
    await expect(indexDialog.getByText("第 2 页", { exact: true })).toBeVisible();
    const indexScroll = indexDialog.locator(".r31-account-index-scroll");
    await indexScroll.evaluate((element) => { element.scrollTop = 120; });
    const scrollBeforeOverview = await indexScroll.evaluate((element) => element.scrollTop);
    await indexDialog.locator(".r31-account-table-row").nth(3).click();
    const pagedOverview = page.getByRole("dialog", { name: "账号活动概览" });
    for (const heading of ["本次调查", "全部已授权调查", "活跃情况", "相关调查分布", "主要评论对象"]) {
      await expect(pagedOverview.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }
    await pagedOverview.getByRole("button", { name: "返回评论账号索引" }).click();
    await expect(indexDialog.getByText("第 2 页", { exact: true })).toBeVisible();
    await expect(sortSelect).toHaveValue("comment_count");
    expect(await indexScroll.evaluate((element) => element.scrollTop)).toBe(scrollBeforeOverview);
    await page.screenshot({
      path: join(screenshotDir, "r31-comment-index-return-state-playwright.png")
    });

    await indexDialog.getByRole("button", { name: "风险评论账号 10" }).click();
    await expect(indexDialog.getByText("基于本次发布报告", { exact: true })).toBeVisible();
    await expect(indexDialog.getByText("当前结果 10", { exact: true })).toBeVisible();
    await expect(indexDialog.locator(".r31-account-table-row")).toHaveCount(10);
    await page.screenshot({
      path: join(screenshotDir, "r31-comment-index-risk-playwright.png")
    });
    await indexDialog.getByRole("button", { name: "关闭" }).click();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(reportUrl, { waitUntil: "networkidle" });
    const mobileSection = page.locator("#report-section-2-3");
    await expect(mobileSection).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await mobileSection.screenshot({
      path: join(screenshotDir, "r31-account-overview-mobile-playwright.png")
    });
    await mobileSection.getByRole("button", { name: /^查看全部跨调查评论账号（88）$/ }).click();
    const mobileDrawer = page.getByRole("dialog", { name: "评论账号索引" });
    await expect.poll(async () => Math.round((await mobileDrawer.boundingBox())?.x ?? -1)).toBe(0);
    await expect(mobileDrawer.getByText("当前结果 88", { exact: true })).toBeVisible();
    await expect(mobileDrawer.locator(".r31-account-table-row")).toHaveCount(20);
    const mobileDrawerBox = await mobileDrawer.boundingBox();
    expect(mobileDrawerBox?.width).toBe(390);
    expect(await mobileDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    const mobileTable = mobileDrawer.locator(".r31-account-table");
    const mobileTableBox = await mobileTable.boundingBox();
    const mobileLastHeaderBox = await mobileTable.locator(".r31-account-table-head > span").last().boundingBox();
    const mobileLastCellBox = await mobileTable.locator(".r31-account-table-row").first().locator("[role=cell]").last().boundingBox();
    expect(mobileTableBox).not.toBeNull();
    expect(mobileLastHeaderBox).not.toBeNull();
    expect(mobileLastCellBox).not.toBeNull();
    expect(mobileLastHeaderBox!.x + mobileLastHeaderBox!.width).toBeLessThanOrEqual(
      mobileTableBox!.x + mobileTableBox!.width
    );
    expect(mobileLastCellBox!.x + mobileLastCellBox!.width).toBeLessThanOrEqual(
      mobileTableBox!.x + mobileTableBox!.width
    );
    await page.screenshot({
      path: join(screenshotDir, "r31-comment-index-mobile-playwright.png")
    });
    await mobileDrawer.locator(".r31-account-table-row").first().click();
    const mobileOverview = page.getByRole("dialog", { name: "账号活动概览" });
    await expect(mobileOverview.getByRole("heading", { name: "本次调查", exact: true })).toBeVisible();
    await expect(mobileOverview.locator(".r31-account-metrics.is-drawer")).toHaveCount(2);
    expect(await mobileOverview.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({
      path: join(screenshotDir, "r31-account-overview-drawer-mobile-playwright.png")
    });
  } finally {
    await browser.close();
  }
});

test("published R3.1 report excludes dynamic commenters from print", async () => {
  test.skip(!baseUrl || !executablePath, "real visual acceptance environment is not configured");

  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto(reportUrl, { waitUntil: "networkidle" });
    await expect(page.locator(".r31-report-paper h1")).toHaveText("麦热依姆古丽监控任务2调查报告");
    await expect(page.locator(".r31-report-section")).toHaveCount(12);
    await expect(page.getByText("已完成独立审核评论 12,133", { exact: true })).toBeVisible();
    await expect(page.getByText("评论自身风险 13", { exact: true })).toBeVisible();

    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".ethnic-report-toolbar")).toBeHidden();
    await expect(page.locator(".r31-report-toc")).toBeHidden();
    await expect(page.locator(".r31-drawer-layer")).toHaveCount(0);
    await expect(page.locator(".r31-dynamic-commenters")).toBeHidden();
    await expect(page.locator(".r31-risk-commenters")).toBeVisible();

    const printLayout = await page.evaluate(() => ({
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
      layoutDisplay: getComputedStyle(document.querySelector(".ethnic-report-layout")!).display,
      paperBorderWidth: getComputedStyle(document.querySelector(".r31-report-paper")!).borderTopWidth,
      sectionBreakInside: getComputedStyle(document.querySelector(".r31-report-section")!).breakInside
    }));
    expect(printLayout).toEqual({
      horizontalOverflow: false,
      layoutDisplay: "block",
      paperBorderWidth: "0px",
      sectionBreakInside: "auto"
    });

    await page.screenshot({ path: screenshotPath, fullPage: true });
  } finally {
    await browser.close();
  }
});
