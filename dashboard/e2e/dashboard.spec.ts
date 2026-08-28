import { expect, test } from '@playwright/test'

test.describe('breakout dashboard', () => {
  let consoleErrors: string[]

  test.beforeEach(({ page }) => {
    consoleErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
  })

  test('renders the overview with verdict and KPI cards', async ({ page }) => {
    await page.goto('/#overview')
    await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible()
    await expect(page.getByText(`Direction: TOO EARLY TO SAY`)).toBeVisible()
    await expect(page.getByText('Studied trade ledger')).toBeVisible()
  })

  test('sidebar navigates between sections', async ({ page }) => {
    await page.goto('/#overview')
    await page.getByRole('link', { name: 'Evidence' }).first().click()
    expect(new URL(page.url()).hash).toBe('#evidence')
    await expect(page.getByRole('heading', { name: 'Evidence' })).toBeAttached()
    await page.waitForTimeout(600)
    await expect(page.getByRole('heading', { name: 'Evidence' })).toBeInViewport()
  })

  test('cluster filter recomputes ledger stats and charts together', async ({ page }) => {
    await page.goto('/#overview')
    const ledger = page.getByTestId('kpi-ledger-value')
    const parse = (s: string | null) => Number((s ?? '').replace(/[^\d.-]/g, ''))
    const allValue = parse(await ledger.textContent())
    expect(allValue).toBeGreaterThan(0)

    await page.getByLabel('Cluster').click()
    await page.getByRole('option', { name: 'micro' }).click()
    const microValue = parse(await ledger.textContent())
    expect(microValue).toBeGreaterThan(0)
    expect(microValue).not.toBe(allValue)

    const curveCard = page.getByText(/studied trades under current filters/)
    await expect(curveCard).toContainText(
      `${microValue.toLocaleString('en-IN')} studied trades`,
    )
  })

  test('exit filter narrows to that exit reason only', async ({ page }) => {
    await page.goto('/#overview')
    await page.getByLabel('Exit reason').click()
    await page.getByRole('option', { name: 'stop' }).click()
    await expect(page.getByTestId('kpi-ledger-label')).toHaveText(
      'Studied trades (filtered)',
    )
  })

  test('date range filter empties cleanly and reset restores', async ({ page }) => {
    await page.goto('/#overview')
    await page.getByLabel('From').fill('2030-01-01')
    const note = page.locator('#overview').getByText('No studied trades match')
    await expect(note).toBeVisible()
    await page.getByRole('button', { name: 'Reset' }).click()
    await expect(note).toHaveCount(0)
  })

  test('simulations table sorts by column and batch filter deep-links', async ({
    page,
  }) => {
    await page.goto('/?batch=impact#overview')
    const firstCagr = await page
      .getByRole('row')
      .nth(1)
      .getByRole('cell')
      .nth(2)
      .textContent()
    await page.getByRole('columnheader', { name: 'CAGR' }).click()
    const firstAfterAsc = await page
      .getByRole('row')
      .nth(1)
      .getByRole('cell')
      .nth(2)
      .textContent()
    expect(firstAfterAsc).not.toBe(firstCagr)

    const simRows = page
      .getByRole('table', { name: 'Recorded simulations' })
      .getByRole('row')
    const count = await simRows.count()
    for (let i = 1; i < count; i++) {
      await expect(simRows.nth(i).getByRole('cell').nth(1)).toContainText('impact')
    }
    expect(count).toBeGreaterThan(2)
  })

  test('gates section lists verdict chips', async ({ page }) => {
    await page.goto('/#overview')
    const gatesNav = page.getByRole('link', { name: 'Gates' }).first()
    await gatesNav.click()
    await expect(page.getByText('PASS').first()).toBeVisible()
    await expect(page.getByText('PENDING').first()).toBeVisible()
  })

  test('evidence charts mount with nonzero height', async ({ page }) => {
    await page.goto('/#overview')
    await page.getByRole('link', { name: 'Evidence' }).first().click()
    await page.waitForTimeout(600)
    const heights = await page.$$eval(
      '#evidence .recharts-responsive-container',
      (els) => els.map((e) => e.clientHeight),
    )
    expect(heights.length).toBeGreaterThanOrEqual(4)
    expect(heights.every((h) => h > 100)).toBe(true)
  })

  test('no console errors on load and while filtering', async ({ page }) => {
    await page.goto('/#overview')
    await page.getByLabel('Cluster').click()
    await page.getByRole('option', { name: 'small' }).click()
    await page.waitForTimeout(300)
    expect(consoleErrors).toEqual([])
  })
})
