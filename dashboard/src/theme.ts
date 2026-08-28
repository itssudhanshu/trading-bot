import { createTheme, alpha } from '@mui/material/styles'

// theme-factory "Modern Minimalist" — Charcoal #36454f, Slate Gray #708090, Light Gray #d3d3d3, White #ffffff
// DejaVu Sans Bold headers / DejaVu Sans body. Kept Upstox #6C2DDB purple as active accent (grayscale has no purple).
export const THEME = {
  charcoal: '#36454f',
  slateGray: '#708090',
  lightGray: '#d3d3d3',
  white: '#ffffff',
  upstoxPurple: '#6C2DDB',
  upstoxGreen: '#0A8A4B',
  upstoxRed: '#E53935',
}

export const CHART = {
  primary: '#36454f',
  cyan: '#708090',
  positive: '#0A8A4B',
  negative: '#E53935',
  grid: '#d3d3d3',
  axis: '#708090',
} as const

export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: THEME.upstoxPurple,
      contrastText: '#ffffff',
    },
    secondary: { main: THEME.slateGray },
    success: { main: CHART.positive },
    error: { main: CHART.negative },
    background: { default: '#ffffff', paper: '#ffffff' },
    text: { primary: THEME.charcoal },
    divider: THEME.lightGray,
  },
  typography: {
    fontFamily:
      '"DejaVu Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    h1: { fontWeight: 700, letterSpacing: '-0.01em' },
    h2: { fontWeight: 700, letterSpacing: '-0.01em' },
    h3: { fontWeight: 700, letterSpacing: '-0.01em' },
    h4: { fontWeight: 700, letterSpacing: '-0.01em' },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: { boxShadow: 'none', backgroundImage: 'none' },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontVariantNumeric: 'tabular-nums' },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: { backgroundImage: 'none' },
      },
    },
  },
})

export const drawerSx = {
  '& .MuiDrawer-paper': {
    width: 240,
    boxSizing: 'border-box',
    bgcolor: THEME.charcoal,
    color: 'rgba(255,255,255,0.82)',
    borderRight: `1px solid ${THEME.lightGray}`,
    '& .MuiListItemIcon-root': { color: 'rgba(255,255,255,0.72)' },
    '& .Mui-selected': {
      bgcolor: alpha(THEME.upstoxPurple, 0.18),
      color: THEME.upstoxPurple,
      '&:hover': { bgcolor: alpha(THEME.upstoxPurple, 0.24) },
    },
    '& .MuiTypography-root:first-of-type': { color: '#ffffff' },
  },
} as const
