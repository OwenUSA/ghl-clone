import { useEffect, useMemo, useRef, useState } from 'react'
import type { Map as LeafletMap } from 'leaflet'
import { LINE } from '../ai/aiUi'
import type { DispatchPlan } from '../../lib/api'
import { visitRange, visitTitle } from '../../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const ORANGE = '#ea580c'
const TECH_COLORS = ['#5b7db1', '#5f9278', '#9a7aa0', '#b0874f']

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
    "'": '&#39;' }[c] as string))
}

/**
 * The plan's routes on a map (2026-10-14). The map library is loaded ONLY when this mounts
 * (`import('leaflet')`), and the card mounts it only after "Show map" — so a chat without a
 * map costs nothing. One day (or the whole week) at a time; booked stops in the technician's
 * colour, suggested stops orange; straight lines between stops (an estimate, not roads).
 */
export function PlanMap({ plan, dates }: { plan: DispatchPlan; dates: string[] }) {
  const box = useRef<HTMLDivElement | null>(null)
  const map = useRef<LeafletMap | null>(null)
  const [day, setDay] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const techs = useMemo(() => Array.from(new Set(plan.days.flatMap((d) => d.techs.map((t) => t.tech)))),
    [plan])
  const color = (tech: string) => TECH_COLORS[Math.max(0, techs.indexOf(tech)) % TECH_COLORS.length]

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const L = (await import('leaflet')).default
        await import('leaflet/dist/leaflet.css')
        if (cancelled || !box.current) return
        if (!map.current) {
          map.current = L.map(box.current, { scrollWheelZoom: false }).setView([26.1, -80.2], 10)
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 17, attribution: '&copy; OpenStreetMap contributors' }).addTo(map.current)
        }
        const m = map.current
        m.eachLayer((layer) => { if (!(layer instanceof L.TileLayer)) m.removeLayer(layer) })
        const bounds: [number, number][] = []
        for (const d of plan.days) {
          if (!dates.includes(d.date) || (day && d.date !== day)) continue
          for (const t of d.techs) {
            const pts = t.visits.filter((v) => v.lat != null && v.lng != null)
            if (!pts.length) continue
            L.polyline(pts.map((v) => [v.lat as number, v.lng as number]),
              { color: color(t.tech), weight: 3, opacity: 0.7 }).addTo(m)
            pts.forEach((v, i) => {
              const sug = !v.existing
              const label = day ? String(i + 1) : d.label.slice(0, 2)
              const html = `<div style="width:${sug ? 24 : 20}px;height:${sug ? 24 : 20}px;border-radius:50%;`
                + `background:${sug ? ORANGE : color(t.tech)};color:#fff;font:700 11px system-ui;`
                + `display:flex;align-items:center;justify-content:center;border:2px solid #fff;`
                + `box-shadow:0 1px 3px #0005">${esc(label)}</div>`
              L.marker([v.lat as number, v.lng as number], {
                icon: L.divIcon({ className: '', html, iconSize: [24, 24], iconAnchor: [12, 12] }),
                zIndexOffset: sug ? 500 : 0 })
                .bindPopup(`<b>${esc(visitTitle(v))}</b><br>${esc(d.label)} · `
                  + `${esc(visitRange(v.start, v.end))} · ${esc(t.tech)}`
                  + `${sug ? '<br><b style="color:#ea580c">Suggested</b>' : '<br>Booked'}`)
                .addTo(m)
              bounds.push([v.lat as number, v.lng as number])
            })
          }
        }
        if (bounds.length) m.fitBounds(bounds, { padding: [30, 30], maxZoom: 13 })
        setTimeout(() => m.invalidateSize(), 50)
      } catch (e) {
        if (!cancelled) setError((e as Error).message || 'The map could not load.')
      }
    })()
    return () => { cancelled = true }
  }, [plan, dates, day]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { map.current?.remove(); map.current = null }, [])

  const shown = plan.days.filter((d) => dates.includes(d.date))
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
        {[{ date: '', label: 'Whole week' }, ...shown].map((d) => (
          <button key={d.date} type="button" onClick={() => setDay(d.date)} aria-pressed={day === d.date}
            style={{ fontSize: 12, padding: '2px 10px', borderRadius: 999, border: `1px solid ${LINE}`,
              background: day === d.date ? '#1f2937' : '#fff', color: day === d.date ? '#fff' : '#1f2937' }}>
            {d.label}</button>
        ))}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 10, fontSize: 12, color: MUTED,
          alignItems: 'center' }}>
          {techs.map((t) => <span key={t} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: color(t) }} />{t}</span>)}
          <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: ORANGE }} />suggested</span>
        </span>
      </div>
      {error ? <div style={{ fontSize: 12, color: MUTED }}>{error}</div>
        : <div ref={box} style={{ height: 420, borderRadius: 10, border: `1px solid ${LINE}` }} />}
      <div style={{ fontSize: 11, color: MUTED, marginTop: 4 }}>
        Straight lines between stops — driving is an estimate, not real roads.</div>
    </div>
  )
}
