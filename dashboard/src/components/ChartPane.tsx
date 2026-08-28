import { useEffect, useRef } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import { createChart, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts'

type OHLCV = { time: string; open: number; high: number; low: number; close: number; volume?: number }

export default function ChartPane({
  data,
  entryPx,
  stop,
  target,
  symbol = 'SOMANYCERA',
  interval = '1D',
}: {
  data: OHLCV[]
  entryPx?: number
  stop?: number
  target?: number
  symbol?: string
  interval?: string
}) {
  const last = data.length ? data[data.length - 1] : null
  const prevClose = data.length > 1 ? data[data.length - 2].close : last?.open ?? 0
  const change = last ? last.close - prevClose : 0
  const changePct = last && prevClose ? (change / prevClose) * 100 : 0
  const isUp = change >= 0
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<any>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#000000' }, textColor: '#D1D4DC' },
      grid: { vertLines: { color: '#1E222D' }, horzLines: { color: '#1E222D' } },
      width: ref.current.clientWidth,
      height: 320,
      timeScale: { borderColor: '#2A2E39', timeVisible: true },
      rightPriceScale: { borderColor: '#2A2E39', scaleMargins: { top: 0.1, bottom: 0.2 } },
      crosshair: { mode: 1 },
    } as any)
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

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', bgcolor: '#000000', border: '1px solid #1E222D', borderRadius: 1, overflow: 'hidden' }}>
      {/* Symbol + OHLC header like TradingView */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 1.5, py: 0.7, bgcolor: '#131722', borderBottom: '1px solid #1E222D', flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ color: '#D1D4DC', fontWeight: 700, fontSize: 12 }}>{symbol} · {interval} · NSE</Typography>
        {last && (
          <>
            <Typography variant="caption" sx={{ color: '#9598A1', fontSize: 11 }}>O{last.open.toFixed(2)} H{last.high.toFixed(2)} L{last.low.toFixed(2)} C{last.close.toFixed(2)}</Typography>
            <Typography variant="caption" sx={{ color: isUp ? '#26A69A' : '#EF5350', fontSize: 11, fontWeight: 600 }}>{isUp ? '+' : ''}{change.toFixed(2)} ({isUp ? '+' : ''}{changePct.toFixed(2)}%)</Typography>
            <Box sx={{ ml: 1, display: 'flex', gap: 0.5 }}>
              <Box sx={{ border: '1px solid #EF5350', color: '#EF5350', px: 0.7, py: 0.2, borderRadius: 0.5, fontSize: 10, lineHeight: 1.2, textAlign: 'center' }}>{last.close.toFixed(2)}<br/><span style={{ fontSize: 9 }}>SELL</span></Box>
              <Box sx={{ border: '1px solid #2962FF', color: '#2962FF', px: 0.7, py: 0.2, borderRadius: 0.5, fontSize: 10, lineHeight: 1.2, textAlign: 'center' }}>{last.close.toFixed(2)}<br/><span style={{ fontSize: 9 }}>BUY</span></Box>
            </Box>
            <Typography variant="caption" sx={{ color: '#9598A1', fontSize: 11, ml: 1 }}>Vol {(last.volume ?? 0).toLocaleString('en-IN')} {isUp ? '▲' : '▼'}</Typography>
          </>
        )}
      </Box>

      {/* Chart canvas */}
      <Box ref={ref} sx={{ flex: 1, minHeight: 320, width: '100%' }} data-testid="chart-pane" />

      {/* Timeframe bar like TradingView bottom */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5, bgcolor: '#131722', borderTop: '1px solid #1E222D', fontSize: 11, color: '#9598A1' }}>
        {['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '5Y', 'All'].map((tf) => (
          <Box key={tf} sx={{ px: 0.8, py: 0.3, borderRadius: 0.5, bgcolor: tf === interval ? '#2962FF' : 'transparent', color: tf === interval ? 'white' : '#9598A1', cursor: 'pointer', fontWeight: tf === interval ? 600 : 400 }}>{tf}</Box>
        ))}
        <Box sx={{ ml: 'auto', display: 'flex', alignItems: 'center', gap: 0.5, color: '#5D606B', fontSize: 10 }}>12:51:00 <span style={{ opacity: 0.5 }}>TradingView</span></Box>
      </Box>
    </Box>
  )
}
