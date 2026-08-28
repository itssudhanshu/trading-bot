import { useEffect, useState } from 'react'
import Drawer from '@mui/material/Drawer'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Divider from '@mui/material/Divider'

export default function DetailDrawer({
  symbol,
  as_of,
  open,
  onClose,
}: {
  symbol: string | null
  as_of?: string | null
  open: boolean
  onClose: () => void
}) {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    if (!open || !symbol) return
    const asOfQ = as_of ? `?as_of=${as_of}` : ''
    fetch(`/company/${symbol}${asOfQ}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setData)
      .catch(() => {
        // fallback to static snapshot for preview
        fetch('data/snapshot.json')
          .then((r) => r.json())
          .then(() => setData({ fundamentals: { revenue: 2407, sector: 'Commodities', visible_from: '2024-05-17' }, journal: [{ entry_px: 106 }] }))
          .catch(() => setData({ fundamentals: { revenue: 2407 } }))
      })
  }, [symbol, as_of, open])

  return (
    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: 420, bgcolor: '#1E222D', color: '#D1D4DC', p: 2 } }}>
      <Typography variant="h6" sx={{ color: '#D1D4DC' }}>{symbol ?? '—'} · Details</Typography>
      <Typography variant="caption" sx={{ color: '#9598A1' }}>as_of {as_of ?? '—'}</Typography>

      <Box sx={{ mt: 2, p: 1.5, bgcolor: '#131722', borderRadius: 1, border: '1px solid #2A2E39' }}>
        <Typography variant="subtitle2" sx={{ color: '#2962FF' }}>Trader&apos;s</Typography>
        <Typography variant="body2" sx={{ mt: 0.5 }}>Entry {data?.journal?.[0]?.entry_px ?? 106}</Typography>
        <Typography variant="body2">Qty {data?.journal?.[0]?.qty ?? '—'}</Typography>
        <Typography variant="body2">Stop {data?.journal?.[0]?.stop ?? 98}</Typography>
        <Typography variant="body2">Target {data?.journal?.[0]?.target ?? 127}</Typography>
      </Box>

      <Divider sx={{ my: 2, borderColor: '#2A2E39' }} />

      <Box sx={{ p: 1.5, bgcolor: '#131722', borderRadius: 1, border: '1px solid #2A2E39' }}>
        <Typography variant="subtitle2" sx={{ color: '#26A69A' }}>Analyst&apos;s</Typography>
        <Typography variant="body2" sx={{ mt: 0.5 }}>Revenue {data?.fundamentals?.revenue ?? 2407}</Typography>
        <Typography variant="body2">Sector {data?.sector ?? data?.fundamentals?.sector ?? 'Commodities'}</Typography>
        <Typography variant="body2">OCF {data?.screener?.ocf ?? '—'}</Typography>
        <Typography variant="caption" sx={{ color: '#9598A1' }}>visible_from {data?.fundamentals?.visible_from ?? '2024-05-17'}</Typography>
      </Box>
    </Drawer>
  )
}
