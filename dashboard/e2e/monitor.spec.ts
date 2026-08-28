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

test('click bucket row opens drawer trader+analyst', async ({ page }) => {
  await page.goto('/#monitor')
  await page.getByTestId('bucket-row').first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('Entry').first()).toBeVisible()
  await expect(page.getByRole('dialog').getByText('Revenue')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('Sector')).toBeVisible()
})
