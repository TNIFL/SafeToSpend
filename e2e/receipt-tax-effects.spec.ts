import fs from "node:fs";
import path from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

test.use({ baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5000" });
test.describe.configure({ mode: "serial" });

const SUMMARY_PATH = path.resolve(process.cwd(), "reports", "receipt_tax_effects_e2e_summary.json");
const FAILURES_PATH = path.resolve(process.cwd(), "reports", "receipt_tax_effects_e2e_failures.json");
const QUERY_KEYS_TO_CLEAR = [
  "receipt_effect_event",
  "receipt_effect_toast",
  "receipt_effect_level",
  "current_tax_due_est_krw",
  "current_buffer_target_krw",
  "tax_delta_from_receipts_krw",
  "buffer_delta_from_receipts_krw",
  "receipt_reflected_expense_krw",
  "receipt_pending_expense_krw",
  "tax_before",
  "tax_after",
  "buffer_before",
  "buffer_after",
  "expense_before",
  "expense_after",
  "profit_before",
  "profit_after",
] as const;

type SeedCase = {
  tx_id: number;
  counterparty: string;
  memo: string;
  amount_krw: number;
  occurred_at: string;
  initial_level: string;
  expectation: string;
};

type SeedSummary = {
  month_key: string;
  credentials: { email: string; password: string; user_pk: number };
  paths: { review: string; tax_buffer: string; calendar: string };
  cases: Record<string, SeedCase>;
  expected_transitions: Record<string, Record<string, string>>;
  e2e_results?: Record<string, unknown>;
};

let failures: Array<Record<string, unknown>> = [];
let runResults: Record<string, unknown> = {};

function readJson<T>(filePath: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8")) as T;
  } catch {
    return fallback;
  }
}

function writeJson(filePath: string, payload: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

function loadSeed(): SeedSummary {
  const seed = readJson<SeedSummary | null>(SUMMARY_PATH, null);
  if (!seed) {
    throw new Error(`seed summary missing: ${SUMMARY_PATH}`);
  }
  return seed;
}

function updateSummary(mutator: (payload: SeedSummary) => SeedSummary): void {
  const current = loadSeed();
  writeJson(SUMMARY_PATH, mutator(current));
}

function currencyNumber(raw: string | null | undefined): number {
  const text = String(raw || "");
  const sign = text.includes("-") ? -1 : 1;
  const digits = text.replace(/[^0-9]/g, "");
  if (!digits) return 0;
  return sign * Number.parseInt(digits, 10);
}

function formatKrw(value: number): string {
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}

function hasStaleEffectParams(rawUrl: string): boolean {
  const url = new URL(rawUrl);
  return QUERY_KEYS_TO_CLEAR.some((key) => url.searchParams.has(key));
}

async function loginTo(page: Page, nextPath: string, seed: SeedSummary): Promise<void> {
  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.locator('input[name="identifier"]').fill(seed.credentials.email);
  await page.locator('input[name="password"]').fill(seed.credentials.password);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
    page.locator('form[action*="/login"] button[type="submit"]').click(),
  ]);
  await page.waitForLoadState("networkidle");
}

async function clearNotifications(page: Page): Promise<void> {
  await page.evaluate(() => {
    try {
      localStorage.setItem("sts_notice_items_v1", "[]");
      localStorage.setItem("sts_notice_hidden_v1", "false");
    } catch (_) {}
    try {
      sessionStorage.clear();
    } catch (_) {}
    const wrap = document.getElementById("global-inline-toast-wrap");
    if (wrap) wrap.innerHTML = "";
    const list = document.getElementById("global-toast-stack");
    if (list) list.innerHTML = "";
    const count = document.getElementById("nav-notice-count");
    if (count) {
      count.textContent = "0";
      count.classList.remove("is-visible");
    }
    const empty = document.getElementById("nav-notice-empty");
    if (empty) empty.textContent = "아직 알림이 없어요.";
  });
}

async function waitForSingleToast(page: Page): Promise<string> {
  const toasts = page.locator("#global-inline-toast-wrap .toast");
  await expect(toasts).toHaveCount(1, { timeout: 6000 });
  return String((await toasts.first().textContent()) || "").trim();
}

async function getNoticeCount(page: Page): Promise<number> {
  const text = await page.locator("#nav-notice-count").textContent();
  return Number.parseInt(String(text || "0"), 10) || 0;
}

async function readAnimated(locator: Locator) {
  const current = Number(await locator.getAttribute("data-tax-current-value"));
  const previous = Number(await locator.getAttribute("data-tax-previous-value"));
  const changed = String((await locator.getAttribute("data-tax-changed")) || "0") === "1";
  return { current, previous, changed };
}

async function expectFinalValue(locator: Locator, expected: number): Promise<void> {
  const prefix = String((await locator.getAttribute("data-tax-prefix")) || "").trim();
  const signedExpected = prefix.startsWith("-") ? -Math.abs(expected) : expected;
  await expect.poll(async () => currencyNumber(await locator.textContent()), { timeout: 3000 }).toBe(signedExpected);
}

async function recordSuccess(caseId: string, payload: Record<string, unknown>): Promise<void> {
  runResults[caseId] = {
    status: "passed",
    ...payload,
  };
  updateSummary((summary) => ({ ...summary, e2e_results: { ...(summary.e2e_results || {}), ...runResults } }));
}

async function recordFailure(caseId: string, payload: Record<string, unknown>): Promise<void> {
  failures.push({ case_id: caseId, ...payload });
  writeJson(FAILURES_PATH, failures);
}

test.afterEach(async ({ page }, testInfo) => {
  const caseId = testInfo.title;
  if (testInfo.status === testInfo.expectedStatus) {
    if (!fs.existsSync(FAILURES_PATH)) writeJson(FAILURES_PATH, failures);
    updateSummary((summary) => ({ ...summary, e2e_results: { ...(summary.e2e_results || {}), ...runResults } }));
    return;
  }
  const screenshotPath = testInfo.outputPath(`${caseId}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await recordFailure(caseId, {
    failed_step: testInfo.titlePath.slice(-1)[0],
    expected: testInfo.expectedStatus,
    actual: testInfo.status,
    screenshot_path: screenshotPath,
    error: testInfo.error?.message || "unknown",
  });
  runResults[caseId] = { status: "failed", screenshot_path: screenshotPath };
  updateSummary((summary) => ({ ...summary, e2e_results: { ...(summary.e2e_results || {}), ...runResults } }));
});

test("followup_reflect_transport", async ({ page }) => {
  const seed = loadSeed();
  const reviewUrl = seed.paths.review;
  const txId = seed.cases.followup_reflect_transport.tx_id;

  await loginTo(page, reviewUrl, seed);
  await clearNotifications(page);

  const row = page.locator(`#tx-${txId}`);
  await expect(row).toBeVisible();

  const taxTarget = page.locator("#taxTarget");
  const beforeTax = currencyNumber(await taxTarget.textContent());

  await row.locator('input[name="followup__weekend_or_late_night_business_reason__text"]').fill("토요일 출장 이동");
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
    row.locator(`form[action$="/review/evidence/${txId}/expense-followup"] button[type="submit"]`).click(),
  ]);
  await page.waitForLoadState("networkidle");

  const toastText = await waitForSingleToast(page);
  expect(toastText).toContain("예상세금이");
  await expect.poll(() => getNoticeCount(page), { timeout: 4000 }).toBe(1);

  const taxMeta = await readAnimated(taxTarget);
  const expenseMeta = await readAnimated(page.locator("#bizExpense"));
  expect(taxMeta.changed).toBeTruthy();
  expect(expenseMeta.changed).toBeTruthy();
  expect(taxMeta.current).toBeLessThan(taxMeta.previous);
  expect(expenseMeta.current).toBeGreaterThan(expenseMeta.previous);
  await expectFinalValue(taxTarget, taxMeta.current);
  await expectFinalValue(page.locator("#bizExpense"), expenseMeta.current);
  expect(beforeTax).toBeGreaterThan(taxMeta.current);
  expect(hasStaleEffectParams(page.url())).toBeFalsy();

  await recordSuccess("followup_reflect_transport", {
    toast: toastText,
    before_tax_due_krw: beforeTax,
    after_tax_due_krw: taxMeta.current,
    reflected_expense_after_krw: expenseMeta.current,
  });
});

test("reinforcement_reflect_meal", async ({ page }) => {
  const seed = loadSeed();
  const reviewUrl = seed.paths.review;
  const txId = seed.cases.reinforcement_reflect_meal.tx_id;

  await loginTo(page, reviewUrl, seed);
  await clearNotifications(page);

  let row = page.locator(`#tx-${txId}`);
  await expect(row).toBeVisible();
  await row.locator('input[name="followup__business_meal_with_client__value"][value="yes"]').check();
  await row.locator('input[name="followup__business_meal_with_client__text"]').fill("A사 미팅 커피");
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
    row.locator(`form[action$="/review/evidence/${txId}/expense-followup"] button[type="submit"]`).click(),
  ]);
  await page.waitForLoadState("networkidle");
  await clearNotifications(page);

  row = page.locator(`#tx-${txId}`);
  const reviewTaxBefore = Number(await page.locator("#taxTarget").getAttribute("data-tax-current-value"));
  const reviewExpenseBefore = Number(await page.locator("#bizExpense").getAttribute("data-tax-current-value"));

  await row.locator('textarea[name="reinforce__business_context_note"]').fill("A사 제안 미팅 중 음료 결제");
  await row.locator('input[name="reinforce__attendee_names"]').fill("A사 김팀장, 박대리");
  await row.locator('input[name="reinforce__client_or_counterparty_name"]').fill("A사");
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
    row.locator(`form[action$="/review/evidence/${txId}/expense-reinforcement"] button[type="submit"]`).click(),
  ]);
  await page.waitForLoadState("networkidle");

  const toastText = await waitForSingleToast(page);
  expect(toastText).toContain("예상세금이");
  await expect.poll(() => getNoticeCount(page), { timeout: 4000 }).toBe(1);

  const reviewTax = page.locator("#taxTarget");
  const reviewExpense = page.locator("#bizExpense");
  const reviewTaxMeta = await readAnimated(reviewTax);
  const reviewExpenseMeta = await readAnimated(reviewExpense);
  expect(reviewTaxMeta.changed).toBeTruthy();
  expect(reviewExpenseMeta.changed).toBeTruthy();
  expect(reviewTaxMeta.current).toBeLessThan(reviewTaxBefore);
  expect(reviewExpenseMeta.current).toBeGreaterThan(reviewExpenseBefore);
  await expectFinalValue(reviewTax, reviewTaxMeta.current);
  await expect(row.getByText("반영된 보강 정보")).toBeVisible();
  await expect(row.getByText("아직 필요한 항목:")).toHaveCount(0);
  expect(hasStaleEffectParams(page.url())).toBeFalsy();

  await page.getByRole("link", { name: "세금 보관함" }).first().click();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("#global-inline-toast-wrap .toast")).toHaveCount(0);
  await expect.poll(() => getNoticeCount(page), { timeout: 4000 }).toBe(1);

  const taxBufferMain = page.locator('div.h1[data-tax-animate="currency"]').first();
  const taxBufferMeta = await readAnimated(taxBufferMain);
  expect(taxBufferMeta.current).toBe(reviewTaxMeta.current);
  expect(taxBufferMeta.changed).toBeTruthy();
  await expectFinalValue(taxBufferMain, taxBufferMeta.current);

  await page.getByRole("link", { name: "캘린더" }).first().click();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("#global-inline-toast-wrap .toast")).toHaveCount(0);

  const calendarTax = page.locator('.kpi.card').filter({ hasText: '세금 추정치(이번 달)' }).locator('[data-tax-animate="currency"]');
  const calendarMeta = await readAnimated(calendarTax);
  expect(calendarMeta.current).toBe(reviewTaxMeta.current);
  expect(calendarMeta.changed).toBeTruthy();
  await expectFinalValue(calendarTax, calendarMeta.current);

  const calendarValueBeforeReload = currencyNumber(await calendarTax.textContent());
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("#global-inline-toast-wrap .toast")).toHaveCount(0);
  expect(hasStaleEffectParams(page.url())).toBeFalsy();
  expect(currencyNumber(await page.locator('.kpi.card').filter({ hasText: '세금 추정치(이번 달)' }).locator('[data-tax-animate="currency"]').textContent())).toBe(calendarValueBeforeReload);

  await recordSuccess("reinforcement_reflect_meal", {
    toast: toastText,
    review_tax_before_krw: reviewTaxBefore,
    review_tax_after_krw: reviewTaxMeta.current,
    review_expense_before_krw: reviewExpenseBefore,
    review_expense_after_krw: reviewExpenseMeta.current,
    tax_buffer_after_krw: taxBufferMeta.current,
    calendar_after_krw: calendarMeta.current,
    notice_count: 1,
  });
});

test("pending_cafe_no_change", async ({ page }) => {
  const seed = loadSeed();
  const reviewUrl = seed.paths.review;
  const txId = seed.cases.pending_cafe_no_change.tx_id;

  await loginTo(page, reviewUrl, seed);
  await clearNotifications(page);

  const row = page.locator(`#tx-${txId}`);
  await expect(row).toBeVisible();
  const taxTarget = page.locator("#taxTarget");
  const beforeTax = Number(await taxTarget.getAttribute("data-tax-current-value"));

  await row.locator('input[name="followup__business_meal_with_client__value"][value="yes"]').check();
  await row.locator('input[name="followup__business_meal_with_client__text"]').fill("회의 준비 커피");
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
    row.locator(`form[action$="/review/evidence/${txId}/expense-followup"] button[type="submit"]`).click(),
  ]);
  await page.waitForLoadState("networkidle");

  const toastText = await waitForSingleToast(page);
  expect(toastText).toContain("아직 예상세금");
  expect(toastText).toContain("반영되지 않았어요");
  await expect.poll(() => getNoticeCount(page), { timeout: 4000 }).toBe(1);

  const taxMeta = await readAnimated(taxTarget);
  expect(taxMeta.current).toBe(beforeTax);
  expect(taxMeta.changed).toBeFalsy();
  await expectFinalValue(taxTarget, taxMeta.current);
  expect(hasStaleEffectParams(page.url())).toBeFalsy();

  await recordSuccess("pending_cafe_no_change", {
    toast: toastText,
    tax_due_krw: taxMeta.current,
    changed: taxMeta.changed,
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("reduced_motion_transport", async ({ page }) => {
    const seed = loadSeed();
    const reviewUrl = seed.paths.review;
    const txId = seed.cases.reduced_motion_transport.tx_id;

    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginTo(page, reviewUrl, seed);
    await clearNotifications(page);

    const row = page.locator(`#tx-${txId}`);
    await expect(row).toBeVisible();
    await row.locator('input[name="followup__weekend_or_late_night_business_reason__text"]').fill("일요일 새벽 공항 출장 이동");
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle", timeout: 15000 }),
      row.locator(`form[action$="/review/evidence/${txId}/expense-followup"] button[type="submit"]`).click(),
    ]);
    await page.waitForLoadState("networkidle");

    const toastText = await waitForSingleToast(page);
    expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBeTruthy();

    const taxTarget = page.locator("#taxTarget");
    const taxMeta = await readAnimated(taxTarget);
    expect(taxMeta.changed).toBeTruthy();
    expect(currencyNumber(await taxTarget.textContent())).toBe(taxMeta.current);
    await page.waitForTimeout(60);
    expect(currencyNumber(await taxTarget.textContent())).toBe(taxMeta.current);
    expect(hasStaleEffectParams(page.url())).toBeFalsy();

    await recordSuccess("reduced_motion_transport", {
      toast: toastText,
      tax_due_krw: taxMeta.current,
      reduced_motion: true,
    });
  });
});
