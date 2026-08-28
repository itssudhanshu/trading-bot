import { useEffect, useRef } from 'react'
import { createChart, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts'

type OHLCV = { time: string; open: number; high: number; low: number; close: number; volume?: number }

export default function ChartPane({
  data,
  entryPx,
  stop,
  target,
}: {
  data: OHLCV[]
  entryPx?: number
  stop?: number
  target?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<any>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#131722' }, textColor: '#D1D4DC' },
      grid: { vertLines: { color: '#1E222D' }, horzLines: { color: '#1E222D' } },
      width: ref.current.clientWidth,
      height: 320,
      timeScale: { borderColor: '#2A2E39' },
      rightPriceScale: { borderColor: '#2A2E39' },
    })
    chartRef.current = chart

    let candleSeries: any
    if ((chart as any).addCandlestickSeries) {
      candleSeries = (chart as any).addCandlestickSeries({ upColor: '#26A69A', downColor: '#EF5350', borderVisible: false, wickUpColor: '#26A69A', wickDownColor: '#EF5350' })
    } else if (CandlestickSeries) {
      candleSeries = (chart as any).addSeries(CandlestickSeries, { upColor: '#26A69A', downColor: '#EF5350', borderVisible: false, wickUpColor: '#26A69A', wickDownColor: '#EF5350' })
    } else {
      candleSeries = (chart as any).addSeries({ type: 'Candlestick' } as any, { upColor: '#26A69A', downColor: '#EF5350' })
    }

    // lightweight-charts expects time as string YYYY-MM-DD or businessDay; our data uses '2024-05-17' which is fine
    candleSeries.setData(data.map((d) => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close })))

    if (entryPx !== undefined) candleSeries.createPriceLine({ price: entryPx, color: '#FFFFFF', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Entry' })
    if (stop !== undefined) candleSeries.createPriceLine({ price: stop, color: '#EF5350', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Stop' })
    if (target !== undefined) candleSeries.createPriceLine({ price: target, color: '#26A69A', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Target' })

    if (data.some((d) => d.volume !== undefined)) {
      let volSeries: any
      if ((chart as any).addHistogramSeries) {
        volSeries = (chart as any).addHistogramSeries({ priceScaleId: '', color: '#26A69A' })
      } else if (HistogramSeries) {
        volSeries = (chart as any).addSeries(HistogramSeries, { priceScaleId: '', color: '#26A69A' })
      } else {
        volSeries = (chart as any).addSeries({ type: 'Histogram' } as any, { priceScaleId: '' })
      }
      volSeries.setData(
        data.map((d) => ({ time: d.time, value: d.volume ?? 0, color: d.close >= d.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)' })),
      )
    }

    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => {
        if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
      })
      ro.observe(ref.current)
    }

    return () => {
      if (ro) ro.disconnect()
      chart.remove()
    }
  }, [data, entryPx, stop, target])

  return <div ref={ref} style={{ height: 320, width: '100%' }} data-testid="chart-pane" />
}
