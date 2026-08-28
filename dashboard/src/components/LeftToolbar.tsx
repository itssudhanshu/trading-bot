import Box from '@mui/material/Box'

export default function LeftToolbar() {
  return (
    <Box
      sx={{
        width: 36,
        bgcolor: '#1E222D',
        borderRight: '1px solid #2A2E39',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 1.5,
        py: 2,
        color: '#9598A1',
        fontSize: 16,
      }}
    >
      <span>✏️</span>
      <span>📏</span>
      <span>🔍</span>
    </Box>
  )
}
