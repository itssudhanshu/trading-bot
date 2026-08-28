import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import { useState, useEffect } from 'react'
import ChartPane from '../components/ChartPane'
import LeftToolbar from '../components/LeftToolbar'
import RightBucketPanel from '../components/RightBucketPanel'
import RightPanel from '../components/RightPanel'
import DetailDrawer from '../components/DetailDrawer'

const SAMPLE = [
  { time: '2024-05-13', open: 100, high: 108, low: 99, close: 106, volume: 1200 },
  { time: '2024-05-14', open: 106, high: 112, low: 104, close: 109, volume: 1500 },
  { time: '2024-05-15', open: 109, high: 115, low: 107, close: 112, volume: 1800 },
  { time: '2024-05-16', open: 112, high: 118, low: 110, close: 116, volume: 2100 },
  { time: '2024-05-17', open: 116, high: 120, low: 114, close: 118, volume: 2500 },
]

export default function Monitor() {
  const [book, setBook] = useState<'main' | 'pooled' | 'etf_trend'>('main')
  const [selected, setSelected] = useState<string | null>('SOMANYCERA')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [ohlc, setOhlc] = useState<any>(null)
  const handleSelect = (sym: string) => {
    setSelected(sym)
    setDrawerOpen(true)
  }

  // Fetch live OHLCV for selected symbol (point-in-time as_of = today for monitor)
  useEffect(() => {
    if (!selected) return
    const asOf = new Date().toISOString().slice(0, 10)
    fetch(`/company/${selected}?as_of=${asOf}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        if (d.prices?.days?.length) {
          const days: string[] = d.prices.days
          const opens: number[] = d.prices.open
          const highs: number[] = d.prices.high
          const lows: number[] = d.prices.low
          const closes: number[] = d.prices.close
          const volumes: number[] = d.prices.volume
          // last 120 days for chart
          const sliced = days.slice(-120).map((t, i) => {
            const idx = days.length - 120 + i
            if (idx < 0) return null
            return { time: t, open: opens[idx], high: highs[idx], low: lows[idx], close: closes[idx], volume: volumes[idx] }
          }).filter(Boolean) as any[]
          if (sliced.length) setOhlc({ data: sliced, last: sliced[sliced.length - 1], prev: sliced[sliced.length - 2], allCloses: closes, allHighs: highs, allLows: lows })
        }
      })
      .catch(() => {})
  }, [selected])
  // derive last OHLC for header + right panel — live if fetched, else SAMPLE
  const chartData = ohlc?.data ?? SAMPLE
  const last = ohlc?.last ?? SAMPLE[SAMPLE.length - 1]
  const prev = ohlc?.prev ?? SAMPLE[SAMPLE.length - 2]
  const allHighs: number[] = ohlc?.allHighs ?? SAMPLE.map((d) => d.high)
  const allLows: number[] = ohlc?.allLows ?? SAMPLE.map((d) => d.low)
  const dayRange = { low: Math.min(...chartData.map((d: any) => d.low)), high: Math.max(...chartData.map((d: any) => d.high)) }
  const week52 = { low: Math.min(...allLows.slice(-252)), high: Math.max(...allHighs.slice(-252)) }

  return (
    <Box sx={{ minHeight: '100dvh', bgcolor: '#000000', color: '#D1D4DC', display: 'flex', flexDirection: 'column' }}>
      {/* Top bar like TradingView screenshot */}
      <Box sx={{ height: 32, display: 'flex', alignItems: 'center', gap: 1, px: 1, bgcolor: '#131722', borderBottom: '1px solid #1E222D', fontSize: 12 }}>
        <Box sx={{ display: 'flex', gap: 0.5 }}><Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: '#FF5F56' }} /><Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: '#FFBD2E' }} /><Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: '#27C93F' }} /></Box>
        <Box sx={{ ml: 1, display: 'flex', alignItems: 'center', gap: 0.5, bgcolor: '#1E222D', borderRadius: 1, px: 1, py: 0.3 }}>
          <Box sx={{ width: 18, height: 18, borderRadius: '50%', bgcolor: '#E53935', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 800 }}>S</Box>
          <Typography variant="caption" sx={{ color: '#D1D4DC', fontWeight: 700 }}>{selected ?? 'SOMANYCERA'} 564.50 +1.42</Typography>
        </Box>
        <Box sx={{ ml: 1, color: '#6A6D78' }}>＋</Box>
        <Box sx={{ ml: 'auto', display: 'flex', gap: 1, color: '#6A6D78', fontSize: 12 }}>◫ &nbsp; … &nbsp; <Box component="span" sx={{ border: '1px solid #2A2E39', px: 1, borderRadius: 1 }}>Unnamed ▾</Box> <span>⚡</span> <span>⬢</span> <span>⛶</span> <span>📷</span> <Box sx={{ bgcolor: '#1E222D', px: 1.2, py: 0.3, borderRadius: 12, border: '1px solid #2A2E39' }}>Trade</Box> <Box sx={{ bgcolor: 'white', color: 'black', px: 1.2, py: 0.3, borderRadius: 12, fontWeight: 700 }}>Publish</Box></Box>
      </Box>

      {/* Second toolbar like screenshot: symbol search, D, candles, indicators */}
      <Box sx={{ height: 36, display: 'flex', alignItems: 'center', gap: 1, px: 1, bgcolor: '#131722', borderBottom: '1px solid #1E222D', fontSize: 12 }}>
        <FormControl size="small" sx={{ minWidth: 120, '& .MuiInputLabel-root': { color: '#9598A1', fontSize: 11 }, '& .MuiOutlinedInput-root': { color: '#D1D4DC', fontSize: 11, height: 28, '& fieldset': { borderColor: '#2A2E39' } } }}>
          <InputLabel id="bucket-label2" sx={{ color: '#9598A1' }}>Bucket</InputLabel>
          <Select labelId="bucket-label2" label="Bucket" value={book} onChange={(e) => setBook(e.target.value as any)} size="small" sx={{ height: 28, fontSize: 11 }}>
            <MenuItem value="main">Bucket</MenuItem>
            <MenuItem value="pooled">Pool</MenuItem>
            <MenuItem value="etf_trend">ETF_trend</MenuItem>
          </Select>
        </FormControl>
        <Box sx={{ display: 'flex', gap: 0.7, color: '#9598A1', ml: 1, fontSize: 12 }}>SOMANYCE &nbsp; D &nbsp; 🕯 &nbsp; 📊 &nbsp; ⊞ &nbsp; 🕒 ◀◀ </Box>
        <Box sx={{ ml: 'auto', display: 'flex', gap: 1, color: '#9598A1' }}>☐ Unnamed ▾ &nbsp; ⚡ ⬢ ⛶ 📷</Box>
      </Box>

      <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <LeftToolbar />
        <Box sx={{ flex: 1, p: 1, bgcolor: '#000000', display: 'flex', flexDirection: 'column', gap: 1 }}>
          <ChartPane data={chartData} symbol={selected ?? 'SOMANYCERA'} entryPx={ohlc?.last?.close ? ohlc.last.close * 0.9 : 106} stop={ohlc?.last?.close ? ohlc.last.close * 0.92 : 98} target={ohlc?.last?.close ? ohlc.last.close * 1.2 : 127} />
        </Box>
        <Box sx={{ width: 320, display: 'flex', flexDirection: 'column', bgcolor: '#000000', borderLeft: '1px solid #1E222D', overflow: 'hidden' }}>
          <Box sx={{ flex: '0 0 auto', maxHeight: '45%', overflow: 'auto', borderBottom: '1px solid #1E222D' }}>
            <RightBucketPanel book={book} selected={selected} onSelect={handleSelect} />
          </Box>
          <Box sx={{ flex: 1, overflow: 'auto' }}>
            <RightPanel symbol={selected ?? 'SOMANYCERA'} ohlc={last} prevClose={prev?.close} dayRange={dayRange} week52={week52} fundamentals={{ revenue: 2407 }} announcements={[{ title: 'Somany Ceramics June-Quarter Consol Net Profit 355.4 Million Rupees' }]} />
          </Box>
        </Box>
      </Box>

      <DetailDrawer symbol={selected ?? 'SOMANYCERA'} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </Box>
  )
}
