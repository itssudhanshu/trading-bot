import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Select from '@mui/material/Select'
import MenuItem from '@mui/material/MenuItem'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import { useState } from 'react'

export default function Monitor() {
  const [book, setBook] = useState<'main' | 'pooled' | 'etf_trend'>('main')
  return (
    <Box sx={{ minHeight: '100dvh', bgcolor: '#131722', color: '#D1D4DC', p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 140, '& .MuiInputLabel-root': { color: '#9598A1' }, '& .MuiOutlinedInput-root': { color: '#D1D4DC', '& fieldset': { borderColor: '#2A2E39' } } }}>
          <InputLabel id="bucket-label" sx={{ color: '#9598A1' }}>Bucket</InputLabel>
          <Select labelId="bucket-label" label="Bucket" value={book} onChange={(e) => setBook(e.target.value as any)} size="small">
            <MenuItem value="main">Bucket</MenuItem>
            <MenuItem value="pooled">Pool</MenuItem>
            <MenuItem value="etf_trend">ETF_trend</MenuItem>
          </Select>
        </FormControl>
        <Typography variant="h6" sx={{ color: '#D1D4DC' }}>NSE: NATCAPSUQ · micro · breakout · 1D</Typography>
      </Box>
      <Typography sx={{ color: '#9598A1' }}>Monitor — {book} book · 5 stocks · bucket monitor (Card C shell)</Typography>
    </Box>
  )
}
