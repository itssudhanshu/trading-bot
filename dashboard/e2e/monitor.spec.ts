import { expect, test } from '@playwright/test'

test('monitor is default', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/#monitor/)
  await expect(page.getByText('NSE:')).toBeVisible()
})
