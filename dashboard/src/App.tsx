import { Suspense, lazy, useEffect, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import List from '@mui/material/List'
import ListItemButton from '@mui/material/ListItemButton'
import ListItemIcon from '@mui/material/ListItemIcon'
import ListItemText from '@mui/material/ListItemText'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import MenuIcon from '@mui/icons-material/Menu'
import DashboardIcon from '@mui/icons-material/DashboardOutlined'
import AccountTreeOutlinedIcon from '@mui/icons-material/AccountTreeOutlined'
import InsightsOutlinedIcon from '@mui/icons-material/InsightsOutlined'
import HistoryEduOutlinedIcon from '@mui/icons-material/HistoryEduOutlined'
import MenuBookOutlinedIcon from '@mui/icons-material/MenuBookOutlined'
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined'
import type { Snapshot } from './lib/types'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DEFAULT_FILTERS, useFilteredTrades } from './lib/filters'
import type { Filters } from './lib/filters'
import { FilterBar } from './components/FilterBar'
import { drawerSx } from './theme'
import { Overview } from './pages/Overview'
import { Approach } from './pages/Approach'
import { fmtDate } from './lib/format'

const Evidence = lazy(() =>
  import('./pages/Evidence').then((m) => ({ default: m.Evidence })),
)
const Lessons = lazy(() =>
  import('./pages/Lessons').then((m) => ({ default: m.Lessons })),
)
const ForwardBook = lazy(() =>
  import('./pages/ForwardBook').then((m) => ({ default: m.ForwardBook })),
)
const Gates = lazy(() =>
  import('./pages/Gates').then((m) => ({ default: m.Gates })),
)

const NAV = [
  { id: 'overview', label: 'Overview', icon: <DashboardIcon /> },
  { id: 'approach', label: 'Approach', icon: <AccountTreeOutlinedIcon /> },
  { id: 'evidence', label: 'Evidence', icon: <InsightsOutlinedIcon /> },
  { id: 'lessons', label: 'Lessons', icon: <HistoryEduOutlinedIcon /> },
  { id: 'book', label: 'Forward book', icon: <MenuBookOutlinedIcon /> },
  { id: 'gates', label: 'Gates', icon: <FactCheckOutlinedIcon /> },
] as const

const DRAWER_WIDTH = 240

function Shell({ snap }: { snap: Snapshot }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState<string>('overview')
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const filtered = useFilteredTrades({ snapshot: snap, filters })

  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.replace('#', '')
      if (NAV.some((n) => n.id === h)) setActive(h)
    }
    window.addEventListener('hashchange', onHash)
    onHash()
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Lazy chunks mount after the browser's native anchor jump, which then lands
  // nowhere. Retry until the target section exists.
  useEffect(() => {
    let raf = 0
    let tries = 0
    const tick = () => {
      const el = document.getElementById(active)
      if (el) {
        el.scrollIntoView()
        return
      }
      if (tries++ < 30) raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [active])

  const navList = (
    <>
      <Toolbar>
        <Typography variant="subtitle1" noWrap component="div" sx={{ fontWeight: 600 }}>
          breakout
          <Typography component="span" variant="subtitle1" sx={{ color: 'text.disabled' }}>
            {' '}
            / dashboard
          </Typography>
        </Typography>
      </Toolbar>
      <Divider />
      <List component="nav" aria-label="dashboard sections">
        {NAV.map((n) => (
          <ListItemButton
            key={n.id}
            href={`#${n.id}`}
            selected={active === n.id}
            aria-current={active === n.id ? 'true' : undefined}
            onClick={() => setOpen(false)}
          >
            <ListItemIcon>{n.icon}</ListItemIcon>
            <ListItemText primary={n.label} />
          </ListItemButton>
        ))}
      </List>
    </>
  )

  return (
    <Box sx={{ display: 'flex', minHeight: '100dvh' }}>
      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{
          zIndex: (t) => t.zIndex.drawer + 1,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Toolbar>
          <IconButton
            aria-label="Open navigation"
            edge="start"
            onClick={() => setOpen(true)}
            sx={{ mr: 2, display: { md: 'none' } }}
          >
            <MenuIcon />
          </IconButton>
          <Typography variant="h6" noWrap component="div" sx={{ flexGrow: 1 }}>
            Breakout · Dashboard
          </Typography>
          <Chip
            size="small"
            label={`Snapshot ${fmtDate(snap.as_of)}`}
            variant="outlined"
            sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
          />
        </Toolbar>
      </AppBar>

      <Drawer
        variant="temporary"
        open={open}
        onClose={() => setOpen(false)}
        ModalProps={{ keepMounted: true }}
        sx={{
          display: { xs: 'block', md: 'none' },
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH },
        }}
      >
        {navList}
      </Drawer>
      <Drawer
        variant="permanent"
        sx={{
          display: { xs: 'none', md: 'block' },
          ...drawerSx,
          '& .MuiDrawer-paper': {
            ...drawerSx['& .MuiDrawer-paper'],
            boxSizing: 'border-box',
            width: DRAWER_WIDTH,
          },
        }}
        open
      >
        {navList}
      </Drawer>

      <Box
        component="main"
        id="main"
        sx={{
          flexGrow: 1,
          p: { xs: 2, sm: 3 },
          minWidth: 0,
          bgcolor: 'transparent',
          ml: { md: `${DRAWER_WIDTH}px` },
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
        }}
      >
        <Toolbar />
        <ErrorBoundary>
          <Suspense
            fallback={
              <Typography aria-live="polite" role="status" sx={{ py: 8, textAlign: 'center' }}>
                Loading…
              </Typography>
            }
          >
            <FilterBar snap={snap} filters={filters} onChange={setFilters} />
            <Overview snap={snap} filtered={filtered} />
            <Approach snap={snap} />
            <Evidence snap={snap} filtered={filtered} />
            <Lessons snap={snap} />
            <ForwardBook snap={snap} />
            <Gates snap={snap} />
          </Suspense>
        </ErrorBoundary>
      </Box>
    </Box>
  )
}

export default function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Primary: live API (FastAPI /snapshot from Gold), fallback: static snapshot.json
    // so `npm run preview` and Playwright e2e work without a running API server.
    fetch('/snapshot')
      .then((r) => {
        if (!r.ok) throw new Error(`API ${r.status}`)
        return r.json() as Promise<Snapshot>
      })
      .catch(() =>
        fetch('data/snapshot.json').then((r) => {
          if (!r.ok) throw new Error(`snapshot request failed (HTTP ${r.status})`)
          return r.json() as Promise<Snapshot>
        }),
      )
      .then(setSnap)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  if (error) {
    return (
      <Box sx={{ maxWidth: 560, mx: 'auto', px: 2, py: 16 }}>
        <Typography variant="h4" component="h1">
          No snapshot found
        </Typography>
        <Typography sx={{ mt: 1, wordBreak: 'break-word' }}>{error}</Typography>
        <Typography sx={{ mt: 3 }} color="text.secondary">
          Fix: run{' '}
          <code translate="no">python3 src/ops/dashboard_export.py</code> from the repo root, or start{' '}
          <code translate="no">dashboard_api.py</code>, then reload.
        </Typography>
      </Box>
    )
  }

  if (!snap) {
    return (
      <Typography aria-live="polite" role="status" sx={{ py: 24, textAlign: 'center' }}>
        Loading…
      </Typography>
    )
  }

  return <Shell snap={snap} />
}
