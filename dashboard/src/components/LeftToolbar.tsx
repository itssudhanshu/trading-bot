import Box from '@mui/material/Box'

const ICONS = [
  '＋', // cross + position
  '⟋', // trend line
  '⧉', // fib
  '◫', // rectangle / patterns
  '⬡', // pitchfork / GUID
  '✎', // brush
  'T', // text
  '☺', // emoji
  '📏', // measure
  '🔍', // zoom
  '🧲', // magnet
  '🔒', // lock
  '👁', // visibility
  '🗑', // trash
]

export default function LeftToolbar() {
  return (
    <Box
      sx={{
        width: 36,
        bgcolor: '#131722',
        borderRight: '1px solid #1E222D',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.8,
        py: 1,
        color: '#9598A1',
        fontSize: 14,
      }}
    >
      {ICONS.map((ic, i) => (
        <Box
          key={i}
          sx={{
            width: 28,
            height: 28,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 1,
            cursor: 'pointer',
            '&:hover': { bgcolor: '#1E222D', color: '#D1D4DC' },
            fontSize: i === 6 ? 12 : 14,
            fontWeight: i === 6 ? 700 : 400,
          }}
        >
          {ic}
        </Box>
      ))}
    </Box>
  )
}
