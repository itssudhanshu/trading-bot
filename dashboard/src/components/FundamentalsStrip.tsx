import Box from '@mui/material/Box'

export default function FundamentalsStrip({ fundamentals }: { fundamentals?: any }) {
  if (!fundamentals) {
    return (
      <Box sx={{ height: 48, bgcolor: '#1E222D', borderTop: '1px solid #2A2E39', display: 'flex', alignItems: 'center', px: 2, fontSize: 11, color: '#9598A1' }}>
        No fundamentals (stale &gt;550d) · Sector — · visible_from —
      </Box>
    )
  }
  return (
    <Box sx={{ height: 48, bgcolor: '#1E222D', borderTop: '1px solid #2A2E39', display: 'flex', alignItems: 'center', px: 2, fontSize: 11, color: '#D1D4DC', gap: 2 }}>
      <span>Revenue {fundamentals.revenue ?? '—'}</span>
      <span>·</span>
      <span>Sector {fundamentals.sector ?? '—'}</span>
      <span>·</span>
      <span>visible_from {fundamentals.visible_from ?? '—'}</span>
    </Box>
  )
}
