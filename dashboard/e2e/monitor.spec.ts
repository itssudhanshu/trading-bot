import { expect, test } from '@playwright/test'

test('monitor is default', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/#monitor/)
  await expect(page.getByText('NSE:')).toBeVisible()
})

test('bucket switch changes rail', async ({ page }) => {
  await page.goto('/#monitor')
  const firstBucket = await page.getByTestId('bucket-row').first().textContent()
  await page.getByLabel('Bucket').click()
  await page.getByRole('option', { name: 'Pool' }).click()
  const firstPool = await page.getByTestId('bucket-row').first().textContent()
  expect(firstPool).not.toBe(firstBucket)
})
