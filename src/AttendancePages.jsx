import { useEffect, useMemo, useRef, useState } from 'react'
import { Bell, Check, Clock3, Coffee, MapPin, Plus, Trash2 } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  createOfficeLocation,
  breakEnd,
  breakStart,
  deleteOfficeLocation,
  getAttendance,
  getAttendanceCorrectionRequests,
  getAttendanceRoster,
  getNotifications,
  getOfficeLocations,
  markNotificationRead,
  requestAttendanceCorrection,
  reviewAttendanceCorrection,
  timeIn,
  timeOut,
  updateOfficeLocation,
} from './services/attendanceService'
import { requestLocation } from './services/attendanceGeolocation'
import { useAuthStore } from './store/useAuthStore'
import StatusBadge from './components/StatusBadge'

function useApiResource(loader, dependencies = []) {
  const loaderRef = useRef(loader)
  const [version, setVersion] = useState(0)
  const [resource, setResource] = useState({ data: null, loading: true, error: '' })
  loaderRef.current = loader
  useEffect(() => {
    let active = true
    setResource((current) => ({ ...current, loading: true, error: '' }))
    loaderRef.current().then((data) => {
      if (active) setResource({ data, loading: false, error: '' })
    }).catch((error) => {
      if (active) setResource({ data: null, loading: false, error: error.response?.data?.detail || error.message || 'Unable to load data.' })
    })
    return () => { active = false }
  }, [version, ...dependencies])
  return { ...resource, retry: () => setVersion((current) => current + 1) }
}

function SectionHeader({ title, action }) {
  return <div className="section-header"><h2>{title}</h2>{action}</div>
}

const outsidePolicies = [
  { value: 'require_reason', label: 'Require a reason' },
  { value: 'block', label: 'Block Time In' },
  { value: 'allow', label: 'Allow and notify' },
]

function manilaToday() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Manila' }).format(new Date())
}

export function AttendancePage() {
  const user = useAuthStore((state) => state.user)
  const resource = useApiResource(async () => {
    const [roster, records, corrections] = await Promise.all([
      getAttendanceRoster(),
      getAttendance(),
      getAttendanceCorrectionRequests(),
    ])
    return { roster, records, corrections }
  })
  const [outsideReason, setOutsideReason] = useState('')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [locationStatus, setLocationStatus] = useState(null)
  const [now, setNow] = useState(Date.now())
  const [requestOpen, setRequestOpen] = useState(false)
  const [requesting, setRequesting] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [requestValues, setRequestValues] = useState({
    record_id: '',
    reason: '',
    change_status: false,
    requested_status: 'present',
    change_clock_in: false,
    requested_clock_in_at: '',
    change_clock_out: false,
    requested_clock_out_at: '',
  })
  const [reviewNotes, setReviewNotes] = useState({})
  const autoEndedBreakId = useRef(null)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const records = resource.data?.records || []
  const roster = resource.data?.roster || []
  const corrections = resource.data?.corrections || []
  const ownRecord = useMemo(
    () => records.find((item) => item.user_id === user.id && item.work_date === manilaToday()),
    [records, user.id],
  )
  const canClock = user.role !== 'admin'
  const isEmployee = ['staff', 'employee'].includes(user.role)
  const canReviewCorrections = ['admin', 'management', 'hr_manager', 'team_leader'].includes(user.role)
  const isOutside = locationStatus?.state === 'outside'
    || (!locationStatus && ownRecord?.is_outside_premises === true)
  const badgeStatus = isOutside
    ? 'Outside'
    : locationStatus?.state === 'checking'
      ? 'Checking'
      : locationStatus?.state === 'inside'
        || (!locationStatus && ownRecord?.is_outside_premises === false)
          ? 'Inside'
          : 'Unknown'
  const statusLabel = locationStatus?.state === 'checking'
    ? 'Checking location…'
    : isOutside
      ? `Outside premises${(locationStatus?.distance ?? ownRecord?.distance_from_office) != null ? `, ${Math.round(locationStatus?.distance ?? ownRecord.distance_from_office)} m away` : ''}`
      : locationStatus?.state === 'inside'
        || (!locationStatus && ownRecord?.is_outside_premises === false)
          ? 'Inside premises'
          : locationStatus?.state === 'unavailable'
            ? 'Location unavailable'
            : 'Location not checked'

  const submitAttendance = async (action) => {
    setBusy(action)
    setLocationStatus({ state: 'checking' })
    setMessage('Getting your location...')
    try {
      const location = await requestLocation()
      if (action === 'time-in') {
        location.outside_reason = outsideReason
        const record = await timeIn(location)
        setLocationStatus({
          state: record.is_outside_premises ? 'outside' : record.is_outside_premises === false ? 'inside' : 'unavailable',
          distance: record.distance_from_office,
        })
        setMessage('Time In recorded.')
      } else {
        const record = await timeOut(location)
        setLocationStatus({
          state: record.is_outside_premises ? 'outside' : record.is_outside_premises === false ? 'inside' : 'unavailable',
          distance: record.distance_from_office,
        })
        setMessage('Time Out recorded.')
      }
      resource.retry()
    } catch (error) {
      if (error.response?.data?.is_outside_premises === true) {
        setLocationStatus({
          state: 'outside',
          distance: error.response.data.distance_from_office,
        })
      } else if (!error.response) {
        setLocationStatus({ state: 'unavailable' })
      } else {
        setLocationStatus(
          ownRecord?.is_outside_premises === true
            ? { state: 'outside', distance: ownRecord.distance_from_office }
            : ownRecord?.is_outside_premises === false
              ? { state: 'inside', distance: ownRecord.distance_from_office }
              : { state: 'unavailable' },
        )
      }
      setMessage(error.response?.data?.detail || error.message || 'Unable to record attendance.')
    } finally {
      setBusy('')
    }
  }

  const canTimeIn = !resource.loading && !ownRecord?.clock_in_at
  const canTimeOut = !resource.loading && Boolean(ownRecord?.clock_in_at && !ownRecord.clock_out_at)
  const activeBreak = ownRecord?.active_break
  useEffect(() => {
    if (
      activeBreak
      && now >= new Date(activeBreak.auto_end_at).getTime()
      && autoEndedBreakId.current !== activeBreak.id
    ) {
      autoEndedBreakId.current = activeBreak.id
      resource.retry()
    }
  }, [activeBreak, now, resource.retry])
  const breakElapsed = activeBreak
    ? Math.max(0, Math.min(Math.floor((now - new Date(activeBreak.started_at).getTime()) / 1000), Math.floor((new Date(activeBreak.auto_end_at).getTime() - new Date(activeBreak.started_at).getTime()) / 1000)))
    : 0
  const ownCorrectionRecords = records.filter((record) => record.user_id === user.id)
  const submitBreak = async (action) => {
    setBusy(action)
    setMessage('')
    try {
      if (action === 'break-start') await breakStart()
      else await breakEnd()
      setMessage(action === 'break-start' ? 'Break started.' : 'Break ended.')
      resource.retry()
    } catch (error) {
      setMessage(error.response?.data?.detail || 'Unable to update your break.')
    } finally {
      setBusy('')
    }
  }
  const submitCorrection = async (event) => {
    event.preventDefault()
    setRequesting(true)
    setRequestError('')
    try {
      await requestAttendanceCorrection({
        ...requestValues,
        record_id: Number(requestValues.record_id),
        requested_clock_in_at: requestValues.change_clock_in ? toManilaIso(requestValues.requested_clock_in_at) : null,
        requested_clock_out_at: requestValues.change_clock_out ? toManilaIso(requestValues.requested_clock_out_at) : null,
      })
      setRequestOpen(false)
      setRequestValues((value) => ({ ...value, reason: '', change_status: false, change_clock_in: false, requested_clock_in_at: '', change_clock_out: false, requested_clock_out_at: '' }))
      setMessage('Correction request submitted for review.')
      resource.retry()
    } catch (error) {
      setRequestError(error.response?.data?.detail || flattenErrors(error.response?.data) || 'Unable to submit correction request.')
    } finally {
      setRequesting(false)
    }
  }
  const reviewCorrection = async (item, status) => {
    try {
      await reviewAttendanceCorrection(item.id, { status, review_note: reviewNotes[item.id] || '' })
      setMessage(`Correction request ${status}.`)
      resource.retry()
    } catch (error) {
      setMessage(error.response?.data?.detail || 'Unable to review correction request.')
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">ATTENDANCE</p><h2>Attendance</h2><p className="muted">Daily attendance and correction requests for your permitted scope.</p></div></div>{canClock && <section className="panel settings-form"><SectionHeader title="Your shift" action={<span className="subtle-label">{ownRecord?.work_date || manilaToday()}</span>} /><StatusBadge status={badgeStatus} label={statusLabel} />{ownRecord?.latitude != null && <p className="muted">Recorded coordinates: {Number(ownRecord.latitude).toFixed(6)}, {Number(ownRecord.longitude).toFixed(6)} · GPS accuracy ±{Math.round(ownRecord.accuracy)} m</p>}{isOutside && <label>Reason if outside premises<textarea rows="2" maxLength="1000" value={outsideReason} onChange={(event) => setOutsideReason(event.target.value)} /></label>}<div className="form-actions attendance-action-group"><button type="button" className="primary-button attendance-time-in" disabled={!canTimeIn || Boolean(busy)} onClick={() => submitAttendance('time-in')}>{busy === 'time-in' ? 'Getting location...' : 'Time In'}</button><div className="attendance-time-out"><button type="button" className="secondary-button" disabled={!canTimeOut || Boolean(busy)} onClick={() => submitAttendance('time-out')}>{busy === 'time-out' ? 'Getting location...' : 'Time Out'}</button>{!canTimeOut && <span>Time in first</span>}</div>{isEmployee && <button type="button" className="secondary-button" disabled={!canTimeOut || Boolean(activeBreak) || Boolean(busy)} onClick={() => submitBreak('break-start')}><Coffee size={16} /> {busy === 'break-start' ? 'Starting...' : 'Break In'}</button>}{isEmployee && <button type="button" className="secondary-button" disabled={!activeBreak || Boolean(busy)} onClick={() => submitBreak('break-end')}><Coffee size={16} /> {busy === 'break-end' ? 'Ending...' : 'Break Out'}</button>}</div>{activeBreak && <div className="inline-feedback" role="status"><strong>Break in progress</strong> · {formatDuration(breakElapsed)} elapsed · scheduled auto-end {formatAttendanceTime(activeBreak.auto_end_at)}</div>}{ownRecord?.breaks?.some((item) => item.auto_ended) && <p className="form-error">A break was automatically ended at {formatAttendanceTime(ownRecord.breaks.find((item) => item.auto_ended).ended_at)}.</p>}{message && <p role="status">{message}</p>}{isEmployee && <div className="form-actions"><button type="button" className="secondary-button" disabled={ownCorrectionRecords.length === 0} onClick={() => { setRequestError(''); setRequestValues((value) => ({ ...value, record_id: String(ownRecord?.id || ownCorrectionRecords[0]?.id || ''), requested_status: ownRecord?.status_code || ownCorrectionRecords[0]?.status_code || 'present' })); setRequestOpen(true) }}>Request attendance correction</button></div>}</section>}<section className="panel list-panel"><SectionHeader title="Daily roster" action={<Clock3 size={18} />} />{resource.loading || resource.error ? <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading attendance...'}</div> : roster.length === 0 ? <p className="muted">No attendance entries found for today.</p> : <div className="table-scroll"><table><thead><tr><th>Name</th><th>Time in</th><th>Time out</th><th>Break time</th><th>Status</th></tr></thead><tbody>{roster.map((record) => <tr key={record.user_id}><td><div className="person-cell"><span className="avatar small">{record.user_name?.slice(0, 1)?.toUpperCase()}</span><span><strong>{record.user_name}</strong><small>{record.role}{record.team ? ` · ${record.team}` : ''}</small></span></div></td><td>{formatAttendanceTime(record.clock_in_at)}</td><td>{formatAttendanceTime(record.clock_out_at)}</td><td>{formatDuration(record.break_seconds || 0)}{record.active_break && <small className="muted"> · on break</small>}{record.breaks?.some((item) => item.auto_ended) && <small className="form-error"> · auto-ended</small>}</td><td><StatusBadge status={record.status} label={record.status} /></td></tr>)}</tbody></table></div>}</section><section className="panel list-panel"><SectionHeader title={isEmployee ? 'My correction requests' : 'Attendance correction requests'} />{resource.loading || resource.error ? <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading correction requests...'}</div> : corrections.length === 0 ? <p className="muted">No correction requests.</p> : <div className="attendance-location-list">{corrections.map((item) => <article className="evaluation-row" key={item.id}><div className="evaluation-row-main"><strong>{item.user_name} · {item.work_date}</strong><span>Requested by {item.requested_by} · {item.status}</span><p>{item.reason}</p><small>{correctionSummary(item)}</small>{canReviewCorrections && item.status === 'pending' && <label>Review note<input value={reviewNotes[item.id] || ''} onChange={(event) => setReviewNotes((current) => ({ ...current, [item.id]: event.target.value }))} /></label>}</div><StatusBadge status={item.status} />{canReviewCorrections && item.status === 'pending' && <div className="profile-actions"><button className="primary-button" onClick={() => reviewCorrection(item, 'approved')}>Approve</button><button className="secondary-button" disabled={!(reviewNotes[item.id] || '').trim()} onClick={() => reviewCorrection(item, 'rejected')}>Reject</button></div>}</article>)}</div>}</section>{requestOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setRequestOpen(false)}><div className="modal" role="dialog" aria-modal="true"><div className="modal-header"><h2>Request attendance correction</h2><button className="icon-button" onClick={() => setRequestOpen(false)} aria-label="Close">×</button></div><form className="modal-form" onSubmit={submitCorrection}><label>Attendance date<select value={requestValues.record_id} required onChange={(event) => { const selected = ownCorrectionRecords.find((row) => row.id === Number(event.target.value)); setRequestValues((value) => ({ ...value, record_id: event.target.value, requested_status: selected?.status_code || 'present' })) }}>{ownCorrectionRecords.map((record) => <option key={record.id} value={record.id}>{record.work_date} · {record.status}</option>)}</select></label><label><input type="checkbox" checked={requestValues.change_status} onChange={(event) => setRequestValues((value) => ({ ...value, change_status: event.target.checked }))} /> Correct status</label>{requestValues.change_status && <label>Requested status<select value={requestValues.requested_status} onChange={(event) => setRequestValues((value) => ({ ...value, requested_status: event.target.value }))}>{[['present', 'On time'], ['late', 'Late'], ['early_out', 'Early out'], ['no_time_out', 'No Time Out'], ['excused', 'Excused'], ['absent', 'Absent']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}<label><input type="checkbox" checked={requestValues.change_clock_in} onChange={(event) => setRequestValues((value) => ({ ...value, change_clock_in: event.target.checked }))} /> Correct Time In</label>{requestValues.change_clock_in && <label>Requested Time In<input type="datetime-local" value={requestValues.requested_clock_in_at} onChange={(event) => setRequestValues((value) => ({ ...value, requested_clock_in_at: event.target.value }))} required /></label>}<label><input type="checkbox" checked={requestValues.change_clock_out} onChange={(event) => setRequestValues((value) => ({ ...value, change_clock_out: event.target.checked }))} /> Correct Time Out</label>{requestValues.change_clock_out && <label>Requested Time Out<input type="datetime-local" value={requestValues.requested_clock_out_at} onChange={(event) => setRequestValues((value) => ({ ...value, requested_clock_out_at: event.target.value }))} required /></label>}<label>Reason<textarea rows="3" maxLength="1000" required value={requestValues.reason} onChange={(event) => setRequestValues((value) => ({ ...value, reason: event.target.value }))} /></label>{requestError && <p className="form-error" role="alert">{requestError}</p>}<div className="form-actions"><button type="button" className="secondary-button" onClick={() => setRequestOpen(false)}>Cancel</button><button className="primary-button" disabled={requesting || !(requestValues.change_status || requestValues.change_clock_in || requestValues.change_clock_out)}>{requesting ? 'Submitting...' : 'Submit request'}</button></div></form></div></div>}</>
}

function formatAttendanceTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, { timeZone: 'Asia/Manila', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function formatDuration(seconds = 0) {
  const minutes = Math.floor(Math.max(0, seconds) / 60)
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function toManilaIso(value) {
  return value ? new Date(`${value}:00+08:00`).toISOString() : null
}

function flattenErrors(data) {
  return data && typeof data === 'object'
    ? Object.values(data).flat().join(' ')
    : ''
}

function correctionSummary(item) {
  const changes = []
  if (item.change_status) changes.push(`Status → ${item.requested_status}`)
  if (item.change_clock_in) changes.push(`Time In → ${formatAttendanceTime(item.requested_clock_in_at)}`)
  if (item.change_clock_out) changes.push(`Time Out → ${formatAttendanceTime(item.requested_clock_out_at)}`)
  return changes.join(' · ')
}

const emptyOffice = {
  name: '',
  latitude: '',
  longitude: '',
  allowed_radius_meters: 100,
  outside_time_in_policy: 'block',
  is_active: true,
}

export function OfficeLocationsPage() {
  const resource = useApiResource(getOfficeLocations)
  const [form, setForm] = useState(emptyOffice)
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const updateForm = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const submit = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    const data = {
      ...form,
      latitude: Number(form.latitude),
      longitude: Number(form.longitude),
      allowed_radius_meters: Number(form.allowed_radius_meters),
    }
    try {
      if (editingId) await updateOfficeLocation(editingId, data)
      else await createOfficeLocation(data)
      setForm(emptyOffice)
      setEditingId(null)
      resource.retry()
    } catch (saveError) {
      setError(Object.values(saveError.response?.data || {}).flat().join(' ') || 'Unable to save office location.')
    } finally {
      setSaving(false)
    }
  }
  const edit = (office) => {
    setEditingId(office.id)
    setForm({ ...office })
  }
  const remove = async (id) => {
    setError('')
    try {
      await deleteOfficeLocation(id)
      resource.retry()
    } catch (deleteError) {
      setError(deleteError.response?.data?.detail || 'Unable to remove office location.')
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">ADMINISTRATION</p><h2>Office locations</h2><p className="muted">Set branch coordinates, the allowed radius and outside-premises time-in policy.</p></div></div><section className="panel settings-form"><SectionHeader title={editingId ? 'Edit office' : 'Add office'} action={<MapPin size={18} />} /><form className="modal-form" onSubmit={submit}><label>Location name<input required maxLength="120" value={form.name} onChange={(event) => updateForm('name', event.target.value)} /></label><div className="form-grid"><label>Latitude<input required type="number" step="any" min="-90" max="90" value={form.latitude} onChange={(event) => updateForm('latitude', event.target.value)} /></label><label>Longitude<input required type="number" step="any" min="-180" max="180" value={form.longitude} onChange={(event) => updateForm('longitude', event.target.value)} /></label><label>Allowed radius (meters)<input required type="number" min="1" value={form.allowed_radius_meters} onChange={(event) => updateForm('allowed_radius_meters', event.target.value)} /></label><label>Outside time-in behavior<select value={form.outside_time_in_policy} onChange={(event) => updateForm('outside_time_in_policy', event.target.value)}>{outsidePolicies.map((policy) => <option value={policy.value} key={policy.value}>{policy.label}</option>)}</select></label></div><label className="preference-row"><span><strong>Active branch</strong><small>Inactive locations are excluded from distance checks.</small></span><input type="checkbox" checked={form.is_active} onChange={(event) => updateForm('is_active', event.target.checked)} /></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions">{editingId && <button type="button" className="secondary-button" onClick={() => { setEditingId(null); setForm(emptyOffice) }}>Cancel edit</button>}<button type="submit" className="primary-button" disabled={saving}>{saving ? 'Saving...' : editingId ? 'Save changes' : <><Plus size={16} /> Add location</>}</button></div></form></section><section className="panel list-panel"><SectionHeader title="Configured branches" action={<span className="subtle-label">{resource.data?.length || 0}</span>} />{resource.loading || resource.error ? <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading offices...'}</div> : (resource.data || []).map((office) => <article className="evaluation-row" key={office.id}><div className="evaluation-row-main"><strong>{office.name}</strong><span>{Number(office.latitude).toFixed(6)}, {Number(office.longitude).toFixed(6)} · {office.allowed_radius_meters} m radius · {outsidePolicies.find((item) => item.value === office.outside_time_in_policy)?.label}</span></div><button type="button" className="secondary-button" onClick={() => edit(office)}>Edit</button><button type="button" className="icon-button" aria-label={`Remove ${office.name}`} onClick={() => remove(office.id)}><Trash2 size={16} /></button></article>)}</section></>
}

export function NotificationsPage() {
  const resource = useApiResource(getNotifications)
  const [error, setError] = useState('')
  const markRead = async (id) => {
    setError('')
    try {
      await markNotificationRead(id)
      window.dispatchEvent(new Event('lexie:notifications-updated'))
      resource.retry()
    } catch (readError) {
      setError(readError.response?.data?.detail || 'Unable to mark the notification as read.')
    }
  }
  if (resource.loading || resource.error) return <div role={resource.error ? 'alert' : 'status'}>{resource.error || 'Loading notifications...'}</div>
  return <><div className="page-heading"><div><p className="eyebrow">INBOX</p><h2>Notifications</h2><p className="muted">{resource.data.unread_count} unread</p></div><Bell size={20} /></div>{error && <p className="form-error" role="alert">{error}</p>}<section className="panel list-panel">{resource.data.results.length ? resource.data.results.map((notification) => <article className="evaluation-row" key={notification.id}><div className="evaluation-row-main"><strong>{notification.title}</strong><span>{new Date(notification.created_at).toLocaleString()}</span><p>{notification.message}</p></div>{!notification.read_at && <button className="secondary-button" onClick={() => markRead(notification.id)}><Check size={16} /> Mark read</button>}</article>) : <div className="empty-state">No notifications yet.</div>}</section></>
}

export function NotificationBell() {
  const user = useAuthStore((state) => state.user)
  const location = useLocation()
  const [refreshVersion, setRefreshVersion] = useState(0)
  useEffect(() => {
    const refresh = () => setRefreshVersion((current) => current + 1)
    window.addEventListener('lexie:notifications-updated', refresh)
    const timer = window.setInterval(refresh, 60000)
    return () => {
      window.removeEventListener('lexie:notifications-updated', refresh)
      window.clearInterval(timer)
    }
  }, [])
  const resource = useApiResource(getNotifications, [location.pathname, refreshVersion])
  if (!user || user.role === 'client' || resource.error) return null
  return <NavLink to={`/${user.role}/notifications`} className="notification-link" aria-label={`Notifications${resource.data?.unread_count ? `, ${resource.data.unread_count} unread` : ''}`} title="Notifications"><Bell size={18} />{resource.data?.unread_count > 0 && <span className="notification-count">{resource.data.unread_count}</span>}</NavLink>
}
