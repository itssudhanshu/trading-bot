import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Chip from '@mui/material/Chip'

type Props = {
  symbol: string | null
  ohlc?: { open: number; high: number; low: number; close: number; volume?: number } | null
  prevClose?: number
  dayRange?: { low: number; high: number }
  week52?: { low: number; high: number }
  fundamentals?: any
  announcements?: any[]
}

export default function RightPanel({ symbol = 'SOMANYCERA', ohlc, prevClose, dayRange, week52, announcements }: Props) {
  const close = ohlc?.close ?? 564.5
  const chg = prevClose ? close - prevClose : 7.9
  const chgPct = prevClose ? (chg / prevClose) * 100 : 1.42
  const isUp = chg >= 0

  return (
    <Box sx={{ width: 320, bgcolor: '#000000', borderLeft: '1px solid #1E222D', display: 'flex', flexDirection: 'column', overflow: 'auto', color: '#D1D4DC' }}>
      {/* Watchlist header like TradingView */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 1.2, borderBottom: '1px solid #1E222D', bgcolor: '#131722' }}>
        <Typography variant="body2" sx={{ fontWeight: 700, color: '#D1D4DC' }}>Watchlist</Typography>
        <Box sx={{ ml: 'auto', display: 'flex', gap: 1, color: '#9598A1', fontSize: 16 }}>＋ &nbsp; ⊞ &nbsp; …</Box>
      </Box>

      {/* Symbol detail card */}
      <Box sx={{ p: 1.5, borderBottom: '1px solid #1E222D' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Box sx={{ width: 28, height: 28, borderRadius: '50%', bgcolor: '#E53935', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontWeight: 800, fontSize: 14 }}>S</Box>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>{symbol}</Typography>
          <Box sx={{ ml: 'auto', display: 'flex', gap: 0.5, color: '#9598A1' }}>⊞ ✎ …</Box>
        </Box>
        <Typography variant="caption" sx={{ color: '#9598A1' }}>{symbol} Ceramics Limited · NSE</Typography>
        <Typography variant="caption" sx={{ color: '#6A6D78', display: 'block' }}>Producer Manufacturing · Building Products</Typography>

        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mt: 1 }}>
          <Typography variant="h5" sx={{ fontWeight: 800, color: '#D1D4DC' }}>{close.toFixed(2)}</Typography>
          <Typography variant="caption" sx={{ color: '#9598A1' }}>INR</Typography>
          <Typography variant="body2" sx={{ color: isUp ? '#26A69A' : '#EF5350', fontWeight: 600 }}>{isUp ? '+' : ''}{chg.toFixed(2)} {isUp ? '+' : ''}{chgPct.toFixed(2)}%</Typography>
        </Box>
        <Typography variant="caption" sx={{ color: '#6A6D78', display: 'flex', alignItems: 'center', gap: 0.5 }}><span style={{ width: 8, height: 4, background: '#6A6D78', display: 'inline-block', borderRadius: 1 }} /> Market closed</Typography>
        <Typography variant="caption" sx={{ color: '#6A6D78' }}>Last update at 15:56 GMT+5:30</Typography>

        {/* Day's Range */}
        <Box sx={{ mt: 1.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#D1D4DC' }}>
            <span>{dayRange?.low.toFixed(2) ?? '553.80'}</span>
            <Typography variant="caption" sx={{ color: '#9598A1', fontSize: 10 }}>DAY&apos;S RANGE</Typography>
            <span>{dayRange?.high.toFixed(2) ?? '568.60'}</span>
          </Box>
          <Box sx={{ height: 4, bgcolor: '#2A2E39', borderRadius: 2, mt: 0.5, position: 'relative' }}>
            <Box sx={{ position: 'absolute', left: '55%', right: '10%', top: 0, bottom: 0, bgcolor: '#4DB6AC', borderRadius: 2 }} />
            <Box sx={{ position: 'absolute', left: '70%', top: -2, width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: '6px solid #D1D4DC' }} />
          </Box>
        </Box>

        {/* 52W Range */}
        <Box sx={{ mt: 1.2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#D1D4DC' }}>
            <span>{week52?.low.toFixed(2) ?? '332.00'}</span>
            <Typography variant="caption" sx={{ color: '#9598A1', fontSize: 10 }}>52WK RANGE</Typography>
            <span>{week52?.high.toFixed(2) ?? '568.60'}</span>
          </Box>
          <Box sx={{ height: 4, bgcolor: '#2A2E39', borderRadius: 2, mt: 0.5, position: 'relative' }}>
            <Box sx={{ position: 'absolute', right: 0, width: '6%', top: 0, bottom: 0, bgcolor: '#4DB6AC', borderRadius: 2 }} />
            <Box sx={{ position: 'absolute', right: 0, top: -2, width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: '6px solid #D1D4DC' }} />
          </Box>
        </Box>

        {/* News */}
        <Box sx={{ mt: 1.5, p: 1.2, bgcolor: '#1A1033', borderRadius: 1.5, border: '1px solid #2A1F5E' }}>
          <Typography variant="caption" sx={{ color: '#7C4DFF', fontWeight: 700 }}>News • Aug 12</Typography>
          <Typography variant="body2" sx={{ color: '#D1D4DC', fontSize: 12, mt: 0.5, lineHeight: 1.4 }}>{announcements?.[0]?.title ?? 'Somany Ceramics June-Quarter Consol Net Profit 355.4 Million Rupees'}</Typography>
          <Typography variant="caption" sx={{ color: '#7C4DFF', mt: 0.5, display: 'block' }}>More events ›</Typography>
        </Box>

        {/* Key stats */}
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 700, color: '#D1D4DC' }}>Key stats</Typography>
          <Box sx={{ mt: 0.8, display: 'flex', flexDirection: 'column', gap: 0.7, fontSize: 11 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#9598A1' }}>Next earnings report</span><span style={{ color: '#D1D4DC', fontWeight: 600 }}>In 69 days</span></Box>
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#9598A1' }}>Volume</span><span>{ohlc?.volume ? `${(ohlc.volume / 1000).toFixed(2)}K` : '184.39K'}</span></Box>
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#9598A1' }}>Average Volume (30D)</span><span>233.87K</span></Box>
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: '#9598A1' }}>Market capitalization</span><span>22.82B</span></Box>
          </Box>
        </Box>

        {/* Earnings scatter like screenshot */}
        <Box sx={{ mt: 1.5, borderTop: '1px solid #1E222D', pt: 1 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>Earnings</Typography>
            <Chip label="69" size="small" sx={{ bgcolor: '#1E222D', color: '#9598A1', border: '1px solid #2A2E39', height: 20, fontSize: 11 }} />
          </Box>
          <Box sx={{ height: 80, position: 'relative', mt: 1, borderLeft: '1px solid #2A2E39', borderBottom: '1px solid #2A2E39', mx: 1 }}>
            {/* y axis labels */}
            <Box sx={{ position: 'absolute', right: -28, top: 0, fontSize: 9, color: '#6A6D78' }}>12.00</Box>
            <Box sx={{ position: 'absolute', right: -28, top: 26, fontSize: 9, color: '#6A6D78' }}>9.00</Box>
            <Box sx={{ position: 'absolute', right: -28, top: 52, fontSize: 9, color: '#6A6D78' }}>6.00</Box>
            <Box sx={{ position: 'absolute', right: -28, bottom: 0, fontSize: 9, color: '#6A6D78' }}>3.00</Box>
            {/* dots like screenshot: teal filled, white outline, red */}
            <Box sx={{ position: 'absolute', left: '10%', bottom: '12%', width: 12, height: 12, borderRadius: '50%', bgcolor: '#EF5350', border: '2px solid #1E222D' }} />
            <Box sx={{ position: 'absolute', left: '22%', bottom: '18%', width: 12, height: 12, borderRadius: '50%', bgcolor: '#4DB6AC', border: '2px solid #1E222D' }} />
            <Box sx={{ position: 'absolute', left: '45%', bottom: '55%', width: 14, height: 14, borderRadius: '50%', bgcolor: '#4DB6AC' }} />
            <Box sx={{ position: 'absolute', left: '62%', bottom: '45%', width: 14, height: 14, borderRadius: '50%', bgcolor: '#4DB6AC' }} />
            <Box sx={{ position: 'absolute', left: '78%', bottom: '70%', width: 12, height: 12, borderRadius: '50%', border: '1px solid #6A6D78', bgcolor: 'transparent' }} />
          </Box>
        </Box>
      </Box>
    </Box>
  )
}
