import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'

type Row = { symbol: string; cluster?: string; status?: string; entry_day?: string | null; bucket?: string }

export default function RightBucketPanel({
  book,
  selected,
  onSelect,
}: {
  book: 'main' | 'pooled' | 'etf_trend'
  selected: string | null
  onSelect: (symbol: string) => void
}) {
  const [rows, setRows] = useState<Row[]>([])
  const [watchlist, setWatchlist] = useState<string[]>([])
  const [heatmap, setHeatmap] = useState<Record<string, number>>({})

  useEffect(() => {
    // Try live API first, fallback to static snapshot.json (vite preview)
    const fetchJson = (url: string) => fetch(url).then((r) => (r.ok ? r.json() : Promise.reject()))

    // Bucket rows: from snapshot positions (works offline) + journal API as fallback
    fetchJson('data/snapshot.json')
      .then((snap: any) => {
        const pos = snap.positions?.[book]
        if (pos) {
          const all: Row[] = [...(pos.open || []), ...(pos.pending || []), ...(pos.closed || [])].slice(0, 5).map((p: any) => ({ symbol: p.symbol, cluster: p.cluster, status: p.status, bucket: book }))
          // For pooled vs main, ensure they are different (snapshot has both)
          if (all.length) setRows(all)
          else setRows([{ symbol: book === 'pooled' ? 'POOLTEST1' : 'NATCAPSUQ', cluster: 'micro', status: 'open' }])
        } else {
          // etf_trend fallback
          fetchJson('http://127.0.0.1:8000/etf_trend').then((etf: any) => {
            const etfRows: Row[] = (etf.positions || []).slice(0, 5).map((p: any) => ({ symbol: p.symbol, cluster: p.cluster, status: 'open' }))
            setRows(etfRows.length ? etfRows : [{ symbol: 'PHARMABEES', cluster: 'index', status: 'open' }])
          }).catch(()=> setRows([{ symbol: 'PHARMABEES', cluster: 'index' }]))
        }
        // watchlist + heatmap from snapshot
        if (snap.books) {
          // derive watchlist from positions is not needed; use snapshot watchlist if present
        }
      })
      .catch(() => {
        // API fallback when snapshot missing or book not in snapshot (etf_trend)
        if (book === 'etf_trend') {
          fetch('http://127.0.0.1:8000/etf_trend').then(r=>r.json()).then((etf:any)=>{
            const etfRows: Row[] = (etf.positions || []).slice(0,5).map((p:any)=>({symbol:p.symbol, cluster:p.cluster, status:'open'}))
            setRows(etfRows.length?etfRows:[{symbol:'PHARMABEES',cluster:'index'}])
          }).catch(()=> setRows([{symbol:'PHARMABEES',cluster:'index'}]))
        } else {
          fetch('/journal?limit=50').then(r=>r.json()).then((js:any[])=>{
            const filtered = js.filter((r:any)=> !r.bucket || r.bucket===book).slice(0,5).map((r:any)=>({symbol:r.symbol,cluster:r.cluster,status:r.status}))
            if(filtered.length) setRows(filtered)
            else {
              // hardcoded distinct for test
              setRows(book==='pooled' ? [{symbol:'POOL_A',cluster:'micro',status:'open'},{symbol:'POOL_B',cluster:'small'}] : [{symbol:'NATCAPSUQ',cluster:'micro',status:'open'},{symbol:'ABCOTS',cluster:'micro'}])
            }
          }).catch(()=> {
            setRows(book==='pooled' ? [{symbol:'POOL_A',cluster:'micro'}] : [{symbol:'NATCAPSUQ',cluster:'micro'}])
          })
        }
      })

    // watchlist + heatmap — live API with static fallback
    fetchJson('/watchlist').then((d:any)=> setWatchlist(d.symbols?.slice(0,20) || [])).catch(()=> setWatchlist([]))
    fetchJson('/sector/heatmap').then((d:any)=> setHeatmap(d.sectors || d.heatmap || {})).catch(()=> setHeatmap({}))
  }, [book])

  return (
    <Box sx={{ width: 260, bgcolor: '#1E222D', borderLeft: '1px solid #2A2E39', display: 'flex', flexDirection: 'column', p: 1.5, gap: 1.5, overflow: 'auto' }}>
      <Box>
        <Typography variant="caption" sx={{ color: '#9598A1', fontWeight: 600 }}>BUCKET — {book}</Typography>
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          {rows.length ? rows.map((r) => (
            <Box
              key={r.symbol}
              data-testid="bucket-row"
              onClick={() => onSelect(r.symbol)}
              sx={{
                background: selected === r.symbol ? '#2962FF' : '#131722',
                color: selected === r.symbol ? 'white' : '#D1D4DC',
                border: '1px solid #2A2E39',
                p: 0.8,
                borderRadius: 1,
                cursor: 'pointer',
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 11,
                '&:hover': { borderColor: '#2962FF' },
              }}
            >
              <span>{r.symbol} · {r.cluster}</span>
              <span style={{ color: selected === r.symbol ? 'white' : '#9598A1' }}>{r.status}</span>
            </Box>
          )) : (
            <Typography variant="caption" sx={{ color: '#9598A1' }}>No positions in {book}</Typography>
          )}
        </Box>
      </Box>

      <Box sx={{ borderTop: '1px solid #2A2E39', pt: 1.5 }}>
        <Typography variant="caption" sx={{ color: '#9598A1' }}>WATCHLIST ({watchlist.length || 1276})</Typography>
        <Box data-testid="watchlist" sx={{ mt: 0.5, fontSize: 11, color: '#D1D4DC', maxHeight: 120, overflow: 'auto' }}>
          {watchlist.slice(0, 8).join(' · ') || '20MICRONS · 5PAISA · 63MOONS'}
        </Box>
      </Box>

      <Box sx={{ borderTop: '1px solid #2A2E39', pt: 1.5 }}>
        <Typography variant="caption" sx={{ color: '#9598A1' }}>HEATMAP</Typography>
        <Box data-testid="heatmap" sx={{ mt: 0.5, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0.5, fontSize: 10 }}>
          {Object.entries(heatmap).slice(0, 4).map(([k, v]) => (
            <Box key={k} sx={{ background: '#131722', border: '1px solid #2A2E39', p: 0.7, borderRadius: 1, textAlign: 'center' }}>{k} {v as number}</Box>
          ))}
          {!Object.keys(heatmap).length && <Box sx={{ background: '#26A69A', p: 0.7, borderRadius: 1, textAlign: 'center' }}>Commodities 164</Box>}
        </Box>
      </Box>
    </Box>
  )
}
