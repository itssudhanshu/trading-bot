import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import { useState } from 'react'
import ChartPane from '../components/ChartPane'

const SAMPLE = [
  { time: '2024-05-13', open: 100, high: 108, low: 99, close: 106, volume: 1200 },
  { time: '2024-05-14', open: 106, high: 112, low: 104, close: 109, volume: 1500 },
  { time: '2024-05-15', open: 109, high: 115, low: 107, close: 112, volume: 1800 },
  { time: '2024-05-16', open: 112, high: 118, low: 110, close: 116, volume: 2100 },
  { time: '2024-05-17', open: 116, high: 120, low: 114, close: 118, volume: 2500 },
]

export default function Monitor() {
  const [book, setBook] = useState<'main' | 'pooled' | 'etf_trend'>('main')
  return (
    <Box sx={{ minHeight: '100dvh', bgcolor: '#131722', color: '#D1D4DC', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 1.5, borderBottom: '1px solid #2A2E39', bgcolor: '#131722' }}>
        <FormControl size="small" sx={{ minWidth: 140, '& .MuiInputLabel-root': { color: '#9598A1' }, '& .MuiOutlinedInput-root': { color: '#D1D4DC', '& fieldset': { borderColor: '#2A2E39' } } }}>
          <InputLabel id="bucket-label" sx={{ color: '#9598A1' }}>Bucket</InputLabel>
          <Select labelId="bucket-label" label="Bucket" value={book} onChange={(e) => setBook(e.target.value as any)} size="small">
            <MenuItem value="main">Bucket</MenuItem>
            <MenuItem value="pooled">Pool</MenuItem>
            <MenuItem value="etf_trend">ETF_trend</MenuItem>
          </Select>
        </FormControl>
        <Typography variant="h6" sx={{ color: '#D1D4DC' }}>NSE: NATCAPSUQ · micro · breakout · 1D</Typography>
        <Box sx={{ ml: 'auto', color: '#9598A1', fontSize: 12 }}>Monitor — {book} · Card C</Box>
      </Box>
      <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <Box sx={{ width: 36, bgcolor: '#1E222D', borderRight: '1px solid #2A2E39', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1.5, py: 2, color: '#9598A1', fontSize: 16 }}>✏️<br/>📏<br/>🔍</Box>
        <Box sx={{ flex: 1, p: 1, bgcolor: '#131722' }}>
          <ChartPane data={SAMPLE} entryPx={106} stop={98} target={127} />
        </Box>
        <Box sx={{ width: 260, bgcolor: '#1E222D', borderLeft: '1px solid #2A2E39', p: 1.5, color: '#9598A1', fontSize: 12 }}>RightBucketPanel (Task 3)</Box>
      </Box>
      <Box sx={{ height: 48, bgcolor: '#1E222D', borderTop: '1px solid #2A2E39', display: 'flex', alignItems: 'center', px: 2, fontSize: 11, color: '#9598A1' }}>Revenue 2407Cr · OCF 80Cr · Sector Commodities · visible_from 2024-05-17 · RightDrawer (Task 4)</Box>
    </Box>
  )
}
