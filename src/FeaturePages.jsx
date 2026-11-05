import { useEffect, useMemo, useState } from 'react'
import { Download, RefreshCw, ShieldCheck } from 'lucide-react'
import { getEmployees } from './services/employeeService'
import { getPredictions } from './services/predictionService'
import {
  deleteProfilePhoto,
  downloadReport,
  getAutomationSettings,
  getEvaluationCriteria,
  getNotificationRecipients,
  getProfilePhoto,
  getReportRecords,
  getTrustedDevices,
  rerunPredictions,
  sendNotification,
  updateAutomationSettings,
  updateEvaluationCriteria,
  updateTrustedDevice,
  uploadProfilePhoto,
} from './services/featureService'
import { useAuthStore } from './store/useAuthStore'

const errorMessage = (error) => error.response?.data?.detail || error.message || 'The request could not be completed.'

function PageState({ loading, error, retry, children }) {
  if (loading) return <div className="empty-state" role="status">Loading...</div>
  if (error) return <div className="empty-state" role="alert"><p>{error}</p><button className="secondary-button" onClick={retry}>Try again</button></div>
  return children
}

function useResource(loader, dependencies = []) {
  const [version, setVersion] = useState(0)
  const [state, setState] = useState({ data: null, loading: true, error: '' })
  useEffect(() => {
    let active = true
    setState((current) => ({ ...current, loading: true, error: '' }))
    loader().then((data) => {
      if (active) setState({ data, loading: false, error: '' })
    }).catch((error) => {
      if (active) setState({ data: null, loading: false, error: errorMessage(error) })
    })
    return () => { active = false }
  }, [version, ...dependencies])
  return { ...state, retry: () => setVersion((value) => value + 1) }
}

export function ReportsPage() {
  const [department, setDepartment] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [exportError, setExportError] = useState('')
  const resource = useResource(async () => {
    const report = await getReportRecords({ department, start_date: startDate, end_date: endDate })
    return { report, departments: report.departments }
  }, [department, startDate, endDate])
  const exportFile = async (format) => {
    setExportError('')
    try {
      await downloadReport(format, { department, start_date: startDate, end_date: endDate })
    } catch (error) {
      setExportError(errorMessage(error))
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">INSIGHT CENTER</p><h2>Performance reports</h2><p className="muted">Filter performance evaluations and export a report.</p></div><div className="profile-actions"><button className="secondary-button" onClick={() => exportFile('csv')}><Download size={15} /> CSV</button><button className="primary-button" onClick={() => exportFile('pdf')}><Download size={15} /> PDF</button></div></div>{exportError && <p className="form-error" role="alert">{exportError}</p>}<section className="panel settings-form"><div className="form-grid"><label>Department<select value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">All departments</option>{resource.data?.departments.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label>From<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>Through<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label></div></section><PageState loading={resource.loading} error={resource.error} retry={resource.retry}>{resource.data && <div className="panel table-panel"><div className="section-header"><h2>{resource.data.report.record_count} evaluations</h2><strong>Average {resource.data.report.average_score.toFixed(1)}</strong></div><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Department</th><th>Date</th><th>Score</th><th>Evaluator</th></tr></thead><tbody>{resource.data.report.rows.map((row) => <tr key={`${row.employee_code}-${row.date}`}><td>{row.employee} <small>{row.employee_code}</small></td><td>{row.department}</td><td>{row.date}</td><td>{Number(row.score).toFixed(1)}</td><td>{row.evaluator}</td></tr>)}</tbody></table>{resource.data.report.rows.length === 0 && <p className="empty-state">No evaluations match these filters.</p>}</div></div>}</PageState></>
}

const automationLabels = {
  scoring_mode: 'Scoring mode',
  score_drop_threshold: 'Low performance alert threshold',
  attendance_time_in_open_time: 'Time-in opens',
  attendance_cutoff_time: 'On-time cutoff',
  attendance_expected_time_out: 'Expected time-out',
  attendance_break_start_time: 'Break starts',
  attendance_break_end_time: 'Break ends',
  attendance_grace_minutes: 'Late grace period (minutes)',
  attendance_on_time_points: 'On-time points',
  attendance_late_points: 'Late points',
  attendance_time_out_points: 'Time-out points',
  attendance_no_time_out_points: 'No time-out points',
  attendance_excused_points: 'Excused absence points',
  attendance_absent_points: 'Absent points',
  attendance_working_days: 'Working weekdays (1=Mon through 7=Sun)',
  attendance_holidays: 'Holidays (comma-separated YYYY-MM-DD)',
}

export function AutomationSettingsPage() {
  const resource = useResource(getAutomationSettings)
  const [values, setValues] = useState({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { if (resource.data) setValues(resource.data) }, [resource.data])
  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setMessage('')
    try {
      setValues(await updateAutomationSettings(values))
      setMessage('Automation settings saved.')
    } catch (error) {
      setMessage(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">SYSTEM SETTINGS</p><h2>Automation settings</h2><p className="muted">Configure evaluation scoring, attendance points, and alert thresholds.</p></div></div><PageState loading={resource.loading} error={resource.error} retry={resource.retry}>{resource.data && <form className="panel settings-form" onSubmit={save}><div className="form-grid">{Object.entries(automationLabels).map(([key, label]) => <label key={key}>{label}{key === 'scoring_mode' ? <select value={values[key] || ''} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}><option value="team_leader">Team Leader ratings</option><option value="automatic">Auto-scored from tracked data</option></select> : <input type={key.includes('_time') ? 'time' : typeof values[key] === 'number' ? 'number' : 'text'} step={typeof values[key] === 'number' && !Number.isInteger(values[key]) ? '0.01' : undefined} value={values[key] ?? ''} onChange={(event) => setValues((current) => ({ ...current, [key]: typeof current[key] === 'number' ? Number(event.target.value) : event.target.value }))} />}</label>)}</div>{message && <p role="status">{message}</p>}<button className="primary-button" type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save automation settings'}</button></form>}</PageState></>
}

export function NotificationComposerPage() {
  const resource = useResource(getNotificationRecipients)
  const [recipientId, setRecipientId] = useState('')
  const [title, setTitle] = useState('')
  const [message, setMessage] = useState('')
  const [feedback, setFeedback] = useState('')
  const [sending, setSending] = useState(false)
  useEffect(() => {
    if (resource.data?.length && !recipientId) setRecipientId(String(resource.data[0].id))
  }, [resource.data, recipientId])
  const send = async (event) => {
    event.preventDefault()
    setSending(true)
    setFeedback('')
    try {
      await sendNotification({ recipient_id: Number(recipientId), title, message })
      setFeedback('Notification sent.')
      setTitle('')
      setMessage('')
    } catch (error) {
      setFeedback(errorMessage(error))
    } finally {
      setSending(false)
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">TEAM COMMUNICATION</p><h2>Send a notification</h2><p className="muted">Compose a message for an account in your permitted team scope.</p></div></div><PageState loading={resource.loading} error={resource.error} retry={resource.retry}><form className="panel settings-form modal-form" onSubmit={send}><label>Recipient<select required value={recipientId} onChange={(event) => setRecipientId(event.target.value)}>{resource.data?.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.email}</option>)}</select></label><label>Title<input required maxLength="160" value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>Message<textarea required rows="6" value={message} onChange={(event) => setMessage(event.target.value)} /></label>{feedback && <p role="status">{feedback}</p>}<button className="primary-button" disabled={sending || !recipientId}>{sending ? 'Sending...' : 'Send notification'}</button></form></PageState></>
}

export function EvaluationCriteriaPage() {
  const user = useAuthStore((state) => state.user)
  const canEdit = ['admin', 'management', 'hr_manager'].includes(user.role)
  const resource = useResource(getEvaluationCriteria)
  const [criteria, setCriteria] = useState({})
  const [feedback, setFeedback] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (resource.data) setCriteria(resource.data) }, [resource.data])
  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setFeedback('')
    try {
      setCriteria(await updateEvaluationCriteria(criteria))
      setFeedback('Evaluation criteria saved.')
    } catch (error) {
      setFeedback(error.response?.data?.detail || errorMessage(error))
    } finally {
      setSaving(false)
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">EVALUATION FRAMEWORK</p><h2>Evaluation criteria</h2><p className="muted">Manage the labels used for the five evaluation score fields.</p></div></div><PageState loading={resource.loading} error={resource.error} retry={resource.retry}>{resource.data && <form className="panel settings-form" onSubmit={save}><div className="form-grid">{Object.entries(criteria).map(([key, label]) => <label key={key}>{key.replace('_score', '').replaceAll('_', ' ')}<input required maxLength="80" readOnly={!canEdit} value={label} onChange={(event) => setCriteria((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>{feedback && <p role="status">{feedback}</p>}{canEdit && <button className="primary-button" disabled={saving}>{saving ? 'Saving...' : 'Save criteria'}</button>}</form>}</PageState></>
}

export function PredictionHistoryPage() {
  const user = useAuthStore((state) => state.user)
  const resource = useResource(async () => {
    const [employees, predictions] = await Promise.all([
      getEmployees({ limit: 200 }),
      getPredictions(undefined, { limit: 200 }),
    ])
    return { employees, predictions }
  })
  const [department, setDepartment] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [rerunning, setRerunning] = useState(false)
  const [feedback, setFeedback] = useState('')
  const departments = useMemo(() => [...new Set((resource.data?.employees || []).map((employee) => employee.department))].sort(), [resource.data])
  const employeeMap = useMemo(() => new Map((resource.data?.employees || []).map((employee) => [employee.id, employee])), [resource.data])
  const rows = useMemo(() => (resource.data?.predictions || []).filter((prediction) => {
    const item = employeeMap.get(prediction.employee_id)
    const day = String(prediction.created_at).slice(0, 10)
    return (!department || item?.department === department)
      && (!startDate || day >= startDate)
      && (!endDate || day <= endDate)
  }), [resource.data, employeeMap, department, startDate, endDate])
  const rerun = async () => {
    setRerunning(true)
    setFeedback('')
    try {
      const result = await rerunPredictions()
      setFeedback(`Batch complete: ${result.completed} succeeded, ${result.failed} failed.${result.errors.length ? ` ${result.errors.slice(0, 3).map((item) => item.detail).join(' ')}` : ''}`)
      resource.retry()
    } catch (error) {
      setFeedback(errorMessage(error))
    } finally {
      setRerunning(false)
    }
  }
  return <><div className="page-heading"><div><p className="eyebrow">MACHINE LEARNING INSIGHTS</p><h2>Prediction history</h2><p className="muted">Prior model outputs and confidence scores.</p></div>{!['staff', 'employee', 'client'].includes(user.role) && <button className="primary-button" onClick={rerun} disabled={rerunning}><RefreshCw size={15} /> {rerunning ? 'Rerunning...' : 'Batch re-run'}</button>}</div>{feedback && <p role="status">{feedback}</p>}<div className="panel settings-form"><div className="form-grid"><label>Department<select value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">All departments</option>{departments.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label>From<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>Through<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label></div></div><PageState loading={resource.loading} error={resource.error} retry={resource.retry}>{resource.data && <div className="panel table-panel"><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Prediction</th><th>Confidence</th><th>Model</th><th>Generated</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{employeeMap.get(row.employee_id)?.name || `Employee #${row.employee_id}`}</td><td>{row.predicted_category}</td><td>{(row.confidence_score * 100).toFixed(2)}%</td><td>{row.model_version || '—'}</td><td>{new Date(row.created_at).toLocaleString()}</td></tr>)}</tbody></table>{!rows.length && <p className="empty-state">No prediction history matches these filters.</p>}</div></div>}</PageState></>
}

export function TrustedDevicesPanel() {
  const resource = useResource(getTrustedDevices)
  const [feedback, setFeedback] = useState('')
  const actOnDevice = async (id, action) => {
    setFeedback('')
    try {
      await updateTrustedDevice(id, action)
      setFeedback(action === 'approve' ? 'Device approved.' : action === 'reject' ? 'Device rejected.' : 'Device revoked.')
      resource.retry()
    } catch (error) {
      setFeedback(errorMessage(error))
    }
  }
  return <section className="panel settings-form"><div className="section-header"><h2>Trusted devices</h2><ShieldCheck size={18} /></div><PageState loading={resource.loading} error={resource.error} retry={resource.retry}>{resource.data && <>{resource.data.map((device) => <div className="preference-row" key={device.id}><span><strong>{device.label}</strong><small>{device.status} · {device.user_name}{device.last_used_at ? ` · Last used ${new Date(device.last_used_at).toLocaleString()}` : ''}</small></span>{device.can_manage && device.status === 'pending' && device.user_id !== useAuthStore.getState().user?.id && <><button className="secondary-button" onClick={() => actOnDevice(device.id, 'approve')}>Approve</button><button className="secondary-button" onClick={() => actOnDevice(device.id, 'reject')}>Reject</button></>}{device.can_manage && device.status !== 'revoked' && <button className="secondary-button" onClick={() => actOnDevice(device.id, 'remove')}>Revoke</button>}</div>)}{resource.data.length === 0 && <p className="muted">No trusted devices are registered for this account.</p>}</>}</PageState>{feedback && <p role="status">{feedback}</p>}</section>
}

export function ProfilePhotoControl() {
  const [photoUrl, setPhotoUrl] = useState('')
  const [feedback, setFeedback] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let active = true
    let currentUrl = ''
    getProfilePhoto().then((url) => {
      currentUrl = url
      if (active) setPhotoUrl(url)
      else URL.revokeObjectURL(url)
    }).catch((error) => {
      if (error.response?.status !== 404 && active) setFeedback(errorMessage(error))
    })
    return () => {
      active = false
      if (currentUrl) URL.revokeObjectURL(currentUrl)
    }
  }, [])
  const upload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    setSaving(true)
    setFeedback('')
    try {
      await uploadProfilePhoto(file)
      const url = await getProfilePhoto()
      setPhotoUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous)
        return url
      })
      setFeedback('Profile photo updated.')
    } catch (error) {
      setFeedback(errorMessage(error))
    } finally {
      setSaving(false)
      event.target.value = ''
    }
  }
  const remove = async () => {
    setSaving(true)
    try {
      await deleteProfilePhoto()
      setPhotoUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous)
        return ''
      })
      setFeedback('Profile photo removed.')
    } catch (error) {
      setFeedback(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }
  return <div className="form-avatar"><div>{photoUrl ? <img className="avatar xl avatar-photo" src={photoUrl} alt="Profile" /> : <span className="avatar xl">+</span>}</div><label className="secondary-button">{saving ? 'Saving...' : 'Upload photo'}<input type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png" hidden onChange={upload} disabled={saving} /></label>{photoUrl && <button type="button" className="text-button" onClick={remove} disabled={saving}>Remove</button>}{feedback && <small role="status">{feedback}</small>}</div>
}
