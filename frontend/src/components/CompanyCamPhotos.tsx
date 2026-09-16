import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { getCompanyCamPhotos, getOpportunityCompanyCam } from '../lib/api'
import {
  UNAVAILABLE, mergePages, neighbours, photoCount, photoStamp, tabMessage,
  type CompanyCamPhoto, type CompanyCamProject,
} from '../lib/companycam'
import {
  BODY, BORDER, BUTTON, DIVIDER, FAINT, HEADING, MUTED, PRIMARY, TEXT, dead,
} from './opportunity/ui'
import { ZuperPhotos } from './ZuperPhotos'

/**
 * CompanyCam photos, in the opportunity modal's style (screenshots 11 and 22).
 *
 *   Photos                                                     [↻ Refresh]
 *   ── <project name> · 12 photos · address          Open in CompanyCam ↗   (several)
 *   ▢ ▢ ▢ ▢
 *   ▢ ▢ ▢ ▢
 *   [                 Load more photos                  ]
 *
 * A thumbnail opens `PhotoViewer`. Every image is a CRM path; the browser never loads
 * CompanyCam directly. Images are `loading="lazy"`, so a 300-photo project fetches
 * what is on screen, and a project is paged 50 at a time.
 *
 * GoHighLevel has no Photos tab, so the layout is OURS, built from the modal's parts:
 * the Notes tab's heading row, its bordered cards and its text-link blue.
 */

const GRID_GAP = 8

export function PhotosTab({ opportunityId }: { opportunityId: number }) {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  // Part of every project's photo query key: a Refresh starts fresh queries whose
  // first page asks the backend past its cache as well.
  const [refreshNonce, setRefreshNonce] = useState(0)
  const projects = useQuery({
    queryKey: ['companycam', opportunityId],
    queryFn: () => getOpportunityCompanyCam(opportunityId),
    staleTime: 60_000,
  })

  const refresh = async () => {
    setRefreshing(true)
    try {
      // `refresh=true` bypasses the backend's short cache too, not only ours.
      const fresh = await getOpportunityCompanyCam(opportunityId, true)
      qc.setQueryData(['companycam', opportunityId], fresh)
      qc.removeQueries({ queryKey: ['companycam-photos', opportunityId] })
      setRefreshNonce((n) => n + 1)
    } catch {
      // The query below renders the failure; nothing else to do here.
      await qc.invalidateQueries({ queryKey: ['companycam', opportunityId] })
    } finally {
      setRefreshing(false)
    }
  }

  const list = projects.data?.projects ?? []
  const message = tabMessage(projects.data?.state, list.length, projects.isError)
  const single = list.length === 1 ? list[0] : null

  return (
    <div>
      <div className="flex items-center gap-3">
        <div style={{ ...HEADING, fontWeight: 600 }} className="flex-1">Photos</div>
        {single?.project_url && <OpenInCompanyCam url={single.project_url} />}
        {projects.data?.state !== 'off' && (
          <button type="button" onClick={refresh} disabled={refreshing || projects.isFetching}
            className="flex items-center gap-2"
            style={{ ...BUTTON, height: 36, padding: '0 14px',
              ...dead(!(refreshing || projects.isFetching)) }}>
            <RefreshIcon spinning={refreshing} />
            Refresh
          </button>
        )}
      </div>

      {projects.isLoading && (
        <div style={{ marginTop: 18, fontSize: 14, color: FAINT }}>Loading photos…</div>
      )}
      {!projects.isLoading && message && (
        <div role={message === UNAVAILABLE ? 'alert' : undefined}
          style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
          {message}
        </div>
      )}

      {!message && list.map((p) => (
        <ProjectPhotos key={p.id} opportunityId={opportunityId} project={p}
          grouped={list.length > 1} refreshNonce={refreshNonce} />
      ))}

      {/* Zuper's job attachments (2026-09-16), next to CompanyCam's; draws nothing unless
          the job is linked and Zuper listed a file. */}
      <ZuperPhotos opportunityId={opportunityId} />
    </div>
  )
}

/** One linked project's photos: a header when a card has several, the grid, paging. */
export function ProjectPhotos({ opportunityId, project, grouped, refreshNonce = 0 }: {
  opportunityId: number
  project: Pick<CompanyCamProject, 'id' | 'name'> & Partial<CompanyCamProject>
  grouped: boolean
  refreshNonce?: number
}) {
  const [open, setOpen] = useState<number | null>(null)
  const pages = useInfiniteQuery({
    queryKey: ['companycam-photos', opportunityId, project.id, refreshNonce],
    queryFn: ({ pageParam }) =>
      getCompanyCamPhotos(opportunityId, project.id, pageParam,
        pageParam === 1 && refreshNonce > 0),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.state === 'ok' && last.has_more ? last.page + 1 : undefined),
    staleTime: 60_000,
  })
  const photos = mergePages(pages.data?.pages ?? [])
  const failed = pages.isError || pages.data?.pages.some((pg) => pg.state !== 'ok')

  return (
    <section aria-label={project.name ?? 'CompanyCam project'} style={{ marginTop: 16 }}>
      {grouped && (
        <div className="flex items-center gap-3"
          style={{ paddingBottom: 8, marginBottom: 12, borderBottom: '1px solid ' + DIVIDER }}>
          <div className="min-w-0 flex-1">
            <div className="truncate" style={{ fontSize: 14, fontWeight: 600, color: BODY }}>
              {project.name ?? 'Untitled project'}
            </div>
            <div className="truncate" style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
              {[photoCount(project.photo_count), project.address].filter(Boolean).join(' · ')}
            </div>
          </div>
          {project.project_url && <OpenInCompanyCam url={project.project_url} />}
        </div>
      )}

      {pages.isLoading && <div style={{ fontSize: 14, color: FAINT }}>Loading photos…</div>}
      {failed && !pages.isLoading && (
        <div role="alert" style={{ fontSize: 14, color: FAINT, textAlign: 'center' }}>
          {UNAVAILABLE}
        </div>
      )}
      {!pages.isLoading && !failed && photos.length === 0 && (
        <div style={{ fontSize: 14, color: FAINT, textAlign: 'center' }}>
          No photos in this project yet.
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: GRID_GAP }}>
        {photos.map((p, i) => (
          <button key={p.id} type="button" onClick={() => setOpen(i)}
            aria-label={`Open photo taken ${photoStamp(p.captured_at)}`}
            title={p.description ?? undefined}
            className="relative block overflow-hidden"
            style={{ aspectRatio: '1 / 1', borderRadius: 8, border: '1px solid ' + BORDER,
              backgroundColor: 'rgb(242,244,247)' }}>
            <Thumb photo={p} />
          </button>
        ))}
      </div>

      {pages.hasNextPage && (
        <button type="button" onClick={() => pages.fetchNextPage()}
          disabled={pages.isFetchingNextPage}
          className="w-full"
          style={{ ...BUTTON, marginTop: 12, height: 36, ...dead(!pages.isFetchingNextPage) }}>
          {pages.isFetchingNextPage ? 'Loading…' : 'Load more photos'}
        </button>
      )}

      {open != null && photos[open] && (
        <PhotoViewer
          photos={photos}
          index={open}
          hasMore={!!pages.hasNextPage}
          projectUrl={project.project_url ?? null}
          projectName={project.name ?? null}
          onIndex={setOpen}
          loadMore={() => pages.fetchNextPage().then(() => undefined)}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  )
}

/**
 * The large viewer: the photo, previous / next (and ← → on the keyboard), when it was
 * taken, who took it, its description, and "Open in CompanyCam". Escape closes it.
 * The annotated image is shown when CompanyCam has one — the backend chooses.
 */
export function PhotoViewer({ photos, index, hasMore, projectUrl, projectName, onIndex,
  loadMore, onClose }: {
  photos: CompanyCamPhoto[]
  index: number
  hasMore: boolean
  projectUrl: string | null
  projectName: string | null
  onIndex: (i: number) => void
  loadMore: () => Promise<void>
  onClose: () => void
}) {
  const photo = photos[index]
  const { prev, next, needsMore } = neighbours(index, photos.length, hasMore)
  const [loading, setLoading] = useState(false)

  const go = useCallback(async (target: number | null, more = false) => {
    if (target == null || loading) return
    if (more) {
      setLoading(true)
      try { await loadMore() } finally { setLoading(false) }
    }
    onIndex(target)
  }, [loading, loadMore, onIndex])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose() }
      else if (e.key === 'ArrowLeft') go(prev)
      else if (e.key === 'ArrowRight') go(next, needsMore)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [go, prev, next, needsMore, onClose])

  if (!photo) return null
  // A portal on <body>: inside the modal the viewer would share the modal's stacking
  // context (z-40) and the status dot (zIndex 70) would sit on its close button.
  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="Photo viewer"
      className="fixed inset-0 flex flex-col"
      // Above the status dot (zIndex 70), which would otherwise sit on the close button.
      style={{ backgroundColor: 'rgba(12,17,29,0.92)', zIndex: 80 }}
      onClick={(e) => { e.stopPropagation(); onClose() }}>
      <div className="flex shrink-0 items-center gap-3" style={{ padding: '14px 20px', color: '#fff' }}
        onClick={(e) => e.stopPropagation()}>
        <div className="min-w-0 flex-1 truncate" style={{ fontSize: 14, fontWeight: 500 }}>
          {projectName ?? 'CompanyCam'}
          <span style={{ color: 'rgb(208,213,221)', fontWeight: 400, marginLeft: 8 }}>
            {index + 1} of {photos.length}{hasMore ? '+' : ''}
          </span>
        </div>
        <button type="button" aria-label="Close photo" onClick={onClose}
          className="flex items-center justify-center" style={{ width: 32, height: 32 }}>
          <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2}
            strokeLinecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>
      </div>

      <div className="relative flex min-h-0 flex-1 items-center justify-center" style={{ padding: '0 72px' }}>
        {prev != null && (
          <NavArrow side="left" label="Previous photo" onClick={() => go(prev)} />
        )}
        <img key={photo.id} src={photo.image_url} alt={textOf(photo.description) ?? 'Job photo'}
          onClick={(e) => e.stopPropagation()}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 4 }} />
        {next != null && (
          <NavArrow side="right" label="Next photo" onClick={() => go(next, needsMore)}
            busy={loading} />
        )}
      </div>

      <div className="shrink-0" onClick={(e) => e.stopPropagation()}
        style={{ padding: '14px 20px 18px', color: '#fff' }}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1" style={{ fontSize: 13 }}>
          <span>{photoStamp(photo.captured_at)}</span>
          <span style={{ color: 'rgb(208,213,221)' }}>
            Photo by {textOf(photo.creator_name) ?? 'unknown'}
          </span>
          {photo.annotated && <span style={{ color: 'rgb(208,213,221)' }}>Annotated</span>}
          <span className="flex-1" />
          {projectUrl && (
            <a href={projectUrl} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-1"
              style={{ fontSize: 14, fontWeight: 500, color: 'rgb(132,173,255)' }}>
              Open in CompanyCam <ExternalIcon color="rgb(132,173,255)" />
            </a>
          )}
        </div>
        {textOf(photo.description) && (
          <div style={{ fontSize: 14, marginTop: 6, whiteSpace: 'pre-wrap', maxHeight: 96,
            overflowY: 'auto' }}>
            {textOf(photo.description)}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

/** Only a string is ever rendered as text; anything else (a CompanyCam object, a number)
 *  is dropped rather than crashing the viewer to a white screen. */
function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

/** A lazy thumbnail; a relay failure reads as a sentence, not a broken-image glyph. */
function Thumb({ photo }: { photo: CompanyCamPhoto }) {
  const [broken, setBroken] = useState(false)
  if (broken) {
    return (
      <span className="flex h-full w-full items-center justify-center"
        style={{ fontSize: 12, color: FAINT, padding: 8, textAlign: 'center' }}>
        Photo unavailable
      </span>
    )
  }
  return (
    <img src={photo.thumbnail_url} alt={photo.description ?? ''} loading="lazy" decoding="async"
      onError={() => setBroken(true)}
      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
  )
}

function NavArrow({ side, label, onClick, busy = false }: {
  side: 'left' | 'right'; label: string; onClick: () => void; busy?: boolean
}) {
  return (
    <button type="button" aria-label={label} disabled={busy}
      onClick={(e) => { e.stopPropagation(); onClick() }}
      className="absolute flex items-center justify-center"
      style={{ [side]: 16, top: '50%', transform: 'translateY(-50%)', width: 44, height: 44,
        borderRadius: 22, backgroundColor: 'rgba(255,255,255,0.14)', ...dead(!busy) }}>
      <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={side === 'left' ? 'm15 18-6-6 6-6' : 'm9 18 6-6-6-6'} />
      </svg>
    </button>
  )
}

export function OpenInCompanyCam({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="flex shrink-0 items-center gap-1"
      style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
      Open in CompanyCam <ExternalIcon color={PRIMARY} />
    </a>
  )
}

function ExternalIcon({ color }: { color: string }) {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3" />
    </svg>
  )
}

function RefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={TEXT} strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      className={spinning ? 'animate-spin' : undefined}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
    </svg>
  )
}

/**
 * One project's photos over whatever page opened it — the contact panel's projects.
 * The photos are read through a card the project is linked to (the backend checks
 * that the reader can see that card), so the dialog names the card(s) it came from.
 */
export function ProjectPhotosDialog({ project, onClose }: {
  project: { id: string; name: string | null; opportunities: { id: number; title: string }[] }
  onClose: () => void
}) {
  const via = project.opportunities[0]
  const details = useQuery({
    queryKey: ['companycam', via.id],
    queryFn: () => getOpportunityCompanyCam(via.id),
    staleTime: 60_000,
  })
  const full = details.data?.projects.find((p) => p.id === project.id)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }} onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={project.name ?? 'CompanyCam project'}
        onClick={(e) => e.stopPropagation()} className="flex flex-col bg-white"
        style={{ width: 904, maxWidth: '96vw', height: 720, maxHeight: '94vh', borderRadius: 12,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div style={{ padding: '24px 24px 0' }}>
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1 truncate"
              style={{ fontSize: 18, fontWeight: 600, color: TEXT, lineHeight: '28px' }}>
              {project.name ?? 'CompanyCam project'}
            </div>
            {full?.project_url && <OpenInCompanyCam url={full.project_url} />}
            <button type="button" aria-label="Close" onClick={onClose}
              className="flex items-center justify-center" style={{ width: 28, height: 28 }}>
              <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={BODY}
                strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="truncate" style={{ fontSize: 14, color: MUTED, marginTop: 8, paddingBottom: 12,
            borderBottom: '1px solid ' + DIVIDER }}>
            {[photoCount(full?.photo_count), full?.address,
              project.opportunities.map((o) => o.title).join(', ')].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: '0 24px 24px' }}>
          {details.data && details.data.state !== 'ok' ? (
            <div role="alert" style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
              {tabMessage(details.data.state, 1)}
            </div>
          ) : (
            <ProjectPhotos opportunityId={via.id} grouped={false}
              project={{ id: project.id, name: project.name, project_url: full?.project_url ?? null }} />
          )}
        </div>
      </div>
    </div>
  )
}
