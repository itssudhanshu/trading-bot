import { render } from '@testing-library/react'
import ChartPane from '../components/ChartPane'

test('chart mounts canvas', () => {
  const { container } = render(
    <ChartPane
      data={[{ time: '2024-05-17', open: 100, high: 110, low: 90, close: 105, volume: 1000 }]}
      entryPx={100}
      stop={90}
      target={120}
    />,
  )
  expect(container.querySelector('canvas')).toBeTruthy()
})
