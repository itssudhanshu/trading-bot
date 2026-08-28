import { expect, test } from '@playwright/test'

test('monitor is default', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/#monitor/)
  await expect(page.getByText('SOMANYCERA').first()).toBeVisible()
})

test('bucket switch changes rail', async ({ page }) => {
  await page.goto('/#monitor')
  const firstBucket = await page.getByTestId('bucket-row').first().textContent()
  await page.getByLabel('Bucket').click()
  await page.getByRole('option', { name: 'Pool' }).click()
  await expect(page.getByText('BUCKET — pooled')).toBeVisible()
  const firstPool = await page.getByTestId('bucket-row').first().textContent()
  expect(firstPool).not.toBe(firstBucket)
})

test('click bucket row opens drawer trader+analyst', async ({ page }) => {
  await page.goto('/#monitor')
  await page.getByTestId('bucket-row').first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('Entry').first()).toBeVisible()
  await expect(page.getByRole('dialog').getByText('Revenue')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('Sector')).toBeVisible()
})

test('ETF_trend shows PHARMABEES and chart mounts', async ({ page }) => {
  await page.goto('/#monitor')
  await page.getByLabel('Bucket').click()
  await page.getByRole('option', { name: 'ETF_trend' }).click()
  await expect(page.getByText('PHARMABEES')).toBeVisible({ timeout: 10000 })
  await expect(page.getByTestId('chart-pane')).toBeVisible()
  await page.waitForTimeout(300)
  const heights = await page.$$eval('[data-testid="chart-pane"]', (els) => els.map((e) => e.clientHeight))
  expect(heights[0]).toBeGreaterThan(200)
})

test('TradingView chrome is present — OHLC, SELL/BUY, Day Range, Key stats', async ({ page }) => {
  await page.goto('/#monitor')
  // OHLC header is inside ChartPane — wait for chart to mount
  await expect(page.getByTestId('chart-pane')).toBeVisible()
  await expect(page.getByText('SELL').first()).toBeVisible({ timeout: 10000 })
  await expect(page.getByText('BUY').first()).toBeVisible()
  await expect(page.getByText("DAY'S RANGE").first()).toBeVisible()
  await expect(page.getByText('52WK RANGE').first()).toBeVisible()
  await expect(page.getByText('Key stats').first()).toBeVisible()
  await expect(page.getByText('Earnings').first()).toBeVisible()
  await expect(page.getByText('Watchlist').first()).toBeVisible()
  await expect(page.getByText('News').first()).toBeVisible()
})
