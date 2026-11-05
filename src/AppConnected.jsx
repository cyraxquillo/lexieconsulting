import { useEffect, useRef, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowDownRight, ArrowUpRight, BarChart3, Bell, BriefcaseBusiness, CheckCircle2, ChevronDown, CircleAlert, ClipboardCheck, Clock3, Download, FileBarChart, FolderUp, LayoutDashboard, LockKeyhole, LogOut, Menu, MoreHorizontal, Plus, Search, Settings, ShieldCheck, Sparkles, UserRound, Users, X } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { getMyClient } from './services/clientService'
import { login as authLogin } from './services/authService'
import { createEmployee, getEmployee, getEmployees, updateEmployee } from './services/employeeService'
import { createEvaluation, deleteEvaluation, getEvaluations, updateEvaluation } from './services/evaluationService'
import { getPredictions, runPrediction } from './services/predictionService'
import { getPerformanceTrend, getSummary } from './services/reportService'
import { createUserAccount, getMe as getUserProfile, getNotificationPrefs, getUserAccounts, updateNotificationPrefs, updatePassword, updateProfile, updateUserAccount } from './services/userService'
import SubmissionsPage from './SubmissionsPage'
import { AttendancePage, NotificationBell, NotificationsPage, OfficeLocationsPage } from './AttendancePages'
import {
  AutomationSettingsPage,
  EvaluationCriteriaPage,
  NotificationComposerPage,
  PredictionHistoryPage,
  ProfilePhotoControl,
  ReportsPage,
  TrustedDevicesPanel,
} from './FeaturePages'
import { getEvaluationCriteria as fetchEvaluationCriteria, submitHybridReview } from './services/featureService'
import StatusBadge from './components/StatusBadge'
import { normalizeRole, roleHome, useAuthStore } from './store/useAuthStore'
import './settings.css'

const cn = (...classes) => classes.filter(Boolean).join(' ')
const roleLabels = { admin: 'Admin', management: 'Management', team_leader: 'Team Leader', staff: 'Staff', client: 'Client' }
const employeeRoleGroups = { management: 'management', hr_manager: 'management', team_leader: 'team_leader', staff: 'staff', employee: 'staff' }
const managementRoles = ['admin', 'management', 'hr_manager']
const staffRoles = ['staff', 'employee']
const accountRoleOptions = {
  admin: [['management', 'Management'], ['team_leader', 'Team Leader'], ['staff', 'Staff']],
  management: [['team_leader', 'Team Leader'], ['staff', 'Staff']],
  hr_manager: [['team_leader', 'Team Leader'], ['staff', 'Staff']],
}
function employeeRoleGroup(role) {
  const normalized = String(role || '').trim().toLowerCase().replace(/[\s-]+/g, '_')
  return employeeRoleGroups[normalized] || normalized
}
function employeeRoleLabel(role) {
  const group = employeeRoleGroup(role)
  if (group === 'management') return 'Management'
  if (group === 'team_leader') return 'Team Leader'
  if (group === 'staff') return 'Staff'
  if (group === 'admin') return 'Admin'
  return role || 'Staff'
}
const rolePath = (role, path = 'overview') => `/${normalizeRole(role)}/${path}`
const trainedDepartments = ['Recruitment', 'Client Services', 'HR & Admin', 'Finance', 'IT Support']

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

function QueryState({ loading, error, onRetry, empty = false, emptyMessage = 'No records found.', children }) {
  if (loading) return <div className="empty-state" role="status"><p>Loading data...</p></div>
  if (error) return <div className="empty-state" role="alert"><h3>We couldn&apos;t load this data</h3><p>{error}</p><button className="secondary-button" onClick={onRetry}>Try again</button></div>
  if (empty) return <div className="empty-state"><h3>Nothing to show yet</h3><p>{emptyMessage}</p></div>
  return children
}

function initials(name = '') { return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() }
function categoryForScore(score = 0) { return score < 70 ? 'At Risk' : score >= 85 ? 'High Performer' : 'Average' }
function averageEvaluation(evaluation) {
  if (!evaluation) return 0
  if (evaluation.overall_score !== undefined) return Number(evaluation.overall_score)
  const fields = ['productivity_score', 'attendance_score', 'quality_score', 'teamwork_score', 'communication_score']
  return fields.reduce((total, field) => total + Number(evaluation[field] || 0), 0) / fields.length
}
function latestBy(items, key, id) {
  return items.filter((item) => item[key] === id).sort((a, b) => new Date(b.date || b.created_at) - new Date(a.date || a.created_at))[0]
}
function presentEmployee(employee, evaluations, predictions) {
  const evaluation = latestBy(evaluations, 'employee_id', employee.id)
  const prediction = latestBy(predictions, 'employee_id', employee.id)
  const score = averageEvaluation(evaluation)
  const role = employee.role || 'staff'
  return {
    ...employee,
    role,
    roleLabel: employeeRoleLabel(role),
    avatar: initials(employee.name),
    score: Math.round(score),
    status: prediction?.predicted_category || categoryForScore(score),
    trend: '—',
    latestEvaluation: evaluation,
  }
}
function formatDate(value, options = { month: 'short', day: 'numeric', year: 'numeric' }) {
  if (!value) return 'Not available'
  return new Date(`${String(value).slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, options)
}
function downloadData(name, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
function Logo() { return <div className="brand" aria-label="Lexie Staffing & Business Consulting"><span className="brand-mark" aria-hidden="true">L</span><span>Lexie<span className="brand-dot">.</span></span></div> }
function Modal({ title, children, onClose }) { return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><div className="modal" role="dialog" aria-modal="true"><div className="modal-header"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></div>{children}</div></div> }
function SectionHeader({ title, action }) { return <div className="section-header"><h2>{title}</h2>{action}</div> }
function StatCard({ label, value, detail, tone = 'green', icon: Icon, trend, onClick }) { return <button className="stat-card stat-card-button" onClick={onClick}><div className="stat-head"><span>{label}</span><span className={cn('stat-icon', tone)}><Icon size={17} /></span></div><strong className="stat-value">{value}</strong><div className="stat-detail">{trend && <span className={trend.startsWith('+') ? 'positive' : 'negative'}>{trend.startsWith('+') ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{trend}</span>}<span>{detail}</span></div></button> }
function useFeedback() { const [message, setMessage] = useState(''); const feedback = (text) => { setMessage(text); setTimeout(() => setMessage(''), 2500) }; return [message, feedback] }

function Login() {
  const login = useAuthStore((state) => state.login)
  const loading = useAuthStore((state) => state.isLoggingIn)
  const loginError = useAuthStore((state) => state.loginError)
  const navigate = useNavigate()
  const [email, setEmail] = useState(import.meta.env.DEV ? 'admin@lexie.ph' : '')
  const [password, setPassword] = useState(import.meta.env.DEV ? 'Admin123!' : '')
  const submit = async (event) => {
    event.preventDefault()
    try {
      const user = await login(email, password)
      navigate(roleHome(user.role), { replace: true })
    } catch {
      // The store exposes the backend's safe authentication message.
    }
  }
  return <div className="login-page"><div className="login-art"><Logo /><div className="art-copy"><p className="eyebrow">PEOPLE &amp; PERFORMANCE</p><h1>Empowering<br /><em>people</em><br />to thrive.</h1><p>People-first staffing and business solutions, powered by a clearer view of performance.</p></div><div className="art-footer"><span>Lexie Staffing &amp; Business Consulting</span><span>© 2026</span></div></div><div className="login-panel"><div className="login-card"><div className="login-heading"><p className="eyebrow">WELCOME BACK</p><h2>Sign in to your workspace</h2><p>Use your Lexie account credentials.</p></div><form onSubmit={submit}><label>Email address<input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label><label>Password<div className="input-icon"><LockKeyhole size={16} /><input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></div></label>{loginError && <p className="form-error" role="alert">{loginError}</p>}<button className="primary-button full" type="submit" disabled={loading}>{loading ? 'Signing in...' : <>Enter workspace <ArrowUpRight size={16} /></>}</button></form>{import.meta.env.DEV && <p className="demo-note"><CheckCircle2 size={14} /> Demo admin credentials are prefilled for local development.</p>}</div></div></div>
}

function Shell({ children }) {
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const isManager = managementRoles.includes(user.role)
  const isTeamLeader = user.role === 'team_leader'
  const nav = [
    ...(isTeamLeader
      ? [{ label: 'My Team', to: rolePath(user.role, 'team'), icon: Users }]
      : [{ label: 'Overview', to: rolePath(user.role), icon: LayoutDashboard }]),
    ...(isManager ? [{ label: 'Employees', to: rolePath(user.role, 'employees'), icon: Users }] : []),
    ...(user.role !== 'client' ? [{ label: 'Attendance', to: rolePath(user.role, 'attendance'), icon: Clock3 }] : []),
    ...(user.role === 'admin' ? [{ label: 'Office locations', to: rolePath(user.role, 'office-locations'), icon: BriefcaseBusiness }] : []),
    ...(user.role !== 'client' ? [{ label: 'Notifications', to: rolePath(user.role, 'notifications'), icon: Bell }] : []),
    ...(isManager || isTeamLeader ? [{ label: 'Compose notification', to: rolePath(user.role, 'compose-notification'), icon: Bell }] : []),
    ...(user.role !== 'client' ? [{ label: 'Work submissions', to: rolePath(user.role, 'submissions'), icon: FolderUp }] : []),
    ...(user.role !== 'client' ? [{ label: 'Evaluations', to: rolePath(user.role, 'evaluations'), icon: ClipboardCheck }, { label: 'Predictions', to: rolePath(user.role, 'predictions'), icon: BarChart3 }] : []),
    ...(isManager ? [{ label: 'User accounts', to: rolePath(user.role, 'accounts'), icon: UserRound }, { label: 'Automation settings', to: rolePath(user.role, 'automation'), icon: Settings }, { label: 'Evaluation criteria', to: rolePath(user.role, 'evaluation-criteria'), icon: ClipboardCheck }] : []),
    ...(isManager || isTeamLeader ? [{ label: 'Reports', to: rolePath(user.role, 'reports'), icon: FileBarChart }] : []),
    { label: 'Settings', to: rolePath(user.role, 'settings'), icon: Settings },
  ]
  const current = nav.find((item) => location.pathname === item.to || location.pathname.startsWith(`${item.to}/`))
  const signOut = () => { logout(); navigate('/login', { replace: true }) }
  return <div className="app-shell"><aside className={cn('sidebar', open && 'open')}><div className="sidebar-top"><Logo /><button className="icon-button close-mobile" onClick={() => setOpen(false)} aria-label="Close navigation"><X size={19} /></button></div><div className="workspace-switcher"><span className="workspace-icon">L</span><span><strong>Lexie workspace</strong><small>{roleLabels[user.role]}</small></span><ChevronDown size={15} /></div><nav>{nav.map(({ label, to, icon: Icon }) => <NavLink key={to} to={to} onClick={() => setOpen(false)} className={({ isActive }) => cn('nav-item', isActive && 'active')}><Icon size={18} /><span>{label}</span></NavLink>)}</nav><div className="sidebar-bottom"><div className="help-card"><div className="help-icon"><Sparkles size={15} /></div><strong>{user.name}</strong><p>{user.email}</p></div><button className="nav-item logout" onClick={signOut}><LogOut size={18} /><span>Sign out</span></button></div></aside><main className="main-area"><header className="topbar"><button className="icon-button menu-button" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={21} /></button><div><p className="breadcrumb">Workspace <span>/</span> {current?.label || 'Workspace'}</p><h1>{current?.label || 'Workspace'}</h1></div><div className="topbar-actions"><NotificationBell /><span className="subtle-label">{user.name}</span></div></header><div className="page-content">{children}</div></main></div>
}

function Dashboard() {
  const user = useAuthStore((state) => state.user)
  if (staffRoles.includes(user.role)) return <StaffDashboard />
  if (user.role === 'client') return <ClientDashboard />
  return <AdminDashboard />
}

function AdminDashboard() {
  const navigateTo = useNavigate()
  const user = useAuthStore((state) => state.user)
  const navigate = (path) => navigateTo(path.replace(/^\/admin/, `/${user.role}`))
  const [range, setRange] = useState('6')
  const resource = useApiResource(async () => {
    const [summary, trend, employees, evaluations, predictions] = await Promise.all([getSummary(), getPerformanceTrend(), getEmployees(), getEvaluations(), getPredictions()])
    return { summary, trend, employees: employees.map((employee) => presentEmployee(employee, evaluations, predictions)), evaluations, predictions }
  })
  const data = resource.data
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  if (!data) return <QueryState loading error="Loading dashboard data..." onRetry={resource.retry} />
  if (!data.employees.length || !data.evaluations.length) return <QueryState empty emptyMessage="Add employees and record evaluations to populate the performance dashboard." />
  const selectedTrend = data?.trend.slice(-Number(range)) || []
  const exportSnapshot = () => downloadData('lexie-performance-snapshot.json', { summary: data.summary, employees: data.employees, generatedAt: new Date().toISOString() })
  return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry}><><div className="welcome-row"><div><p className="eyebrow">PERFORMANCE INTELLIGENCE</p><h2>Good morning, {useAuthStore.getState().user?.name}<span> ✦</span></h2><p className="muted">Here&apos;s what&apos;s happening across your team today.</p></div><button className="secondary-button" onClick={exportSnapshot}><Download size={16} /> Export snapshot</button></div><div className="stats-grid"><StatCard label="Total employees" value={data.summary.active_employee_count} detail="Active employees" icon={Users} onClick={() => navigate('/admin/employees')} /><StatCard label="Avg. performance score" value={Number(data.summary.average_score).toFixed(1)} detail="Latest available evaluations" tone="blue" icon={BarChart3} onClick={() => navigate('/admin/reports')} /><StatCard label="At-risk employees" value={data.summary.at_risk_count} detail="Needs attention this cycle" tone="orange" icon={CircleAlert} onClick={() => navigate('/admin/predictions')} /><StatCard label="Recent evaluations" value={data.evaluations.length} detail="Recorded evaluations" tone="purple" icon={ClipboardCheck} onClick={() => navigate('/admin/evaluations')} /></div><div className="dashboard-grid"><div className="panel chart-panel"><SectionHeader title="Performance trend" action={<select className="select-button" value={range} onChange={(event) => setRange(event.target.value)}><option value="3">Last 3 months</option><option value="6">Last 6 months</option><option value="12">Last 12 months</option></select>} /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><LineChart data={selectedTrend} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}><CartesianGrid vertical={false} stroke="#e6edf0" /><XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#84949c', fontSize: 12 }} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fill: '#84949c', fontSize: 12 }} /><Tooltip /><Line type="monotone" dataKey="score" stroke="#0e8f88" strokeWidth={3} dot={{ r: 4 }} /></LineChart></ResponsiveContainer></div></div><div className="panel chart-panel"><SectionHeader title="Department comparison" action={<span className="subtle-label">Latest average</span>} /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.summary.department_breakdown} margin={{ top: 12, right: 0, left: -25, bottom: 0 }}><CartesianGrid vertical={false} stroke="#e6edf0" /><XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: '#84949c', fontSize: 11 }} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fill: '#84949c', fontSize: 12 }} /><Tooltip /><Bar dataKey="score" fill="#123b4a" radius={[4, 4, 0, 0]} barSize={22} /></BarChart></ResponsiveContainer></div></div></div><TeamPulse employees={data.employees} /></></QueryState>
}

function TeamPulse({ employees }) {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  return <div className="panel table-panel"><SectionHeader title="Team pulse" action={<button className="text-button" onClick={() => navigate(rolePath(user.role, 'employees'))}>View all employees <ArrowUpRight size={15} /></button>} /><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Department</th><th>Score</th><th>Prediction</th><th>Last evaluation</th><th /></tr></thead><tbody>{employees.slice(0, 5).map((employee) => { const path = rolePath(user.role, `employees/${employee.id}`); return <tr key={employee.id} onClick={() => navigate(path)} className="clickable-row"><td><div className="person-cell"><span className="avatar small">{employee.avatar}</span><span><strong>{employee.name}</strong><small>{employee.role}</small></span></div></td><td>{employee.department}</td><td><strong>{employee.score}</strong><span className="score-max">/100</span></td><td><StatusBadge status={employee.status} /></td><td>{formatDate(employee.latestEvaluation?.date)}</td><td><button className="icon-button" aria-label={`Open ${employee.name}`} onClick={(event) => { event.stopPropagation(); navigate(path) }}><ArrowUpRight size={15} /></button></td></tr> })}</tbody></table></div></div>
}

function StaffDashboard() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const resource = useApiResource(async () => {
    const [employees, evaluations, predictions] = await Promise.all([getEmployees(), getEvaluations(), getPredictions()])
    return { employees: employees.map((employee) => presentEmployee(employee, evaluations, predictions)), evaluations, predictions }
  })
  const data = resource.data
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  const employee = data.employees[0]
  if (!employee) return <QueryState empty emptyMessage="No employee record is linked to this account yet." />
  if (!data.evaluations.length) return <QueryState empty emptyMessage="No evaluations have been recorded for your employee account yet." />
  const latestEvaluation = latestBy(data.evaluations, 'employee_id', employee.id)
  const prediction = latestBy(data.predictions, 'employee_id', employee.id)
  const trend = [...data.evaluations].sort((a, b) => new Date(a.date) - new Date(b.date)).map((item) => ({ month: formatDate(item.date, { month: 'short' }), score: averageEvaluation(item) }))
  return <><div className="welcome-row"><div><p className="eyebrow">YOUR PERFORMANCE HUB</p><h2>Welcome, {user.name}</h2><p className="muted">Your latest performance signals and feedback.</p></div><button className="secondary-button" onClick={() => downloadData('my-performance-summary.json', { employee, latestEvaluation, prediction })}><Download size={16} /> Download summary</button></div><div className="staff-highlight"><div><p className="eyebrow">PREDICTED OUTLOOK</p><h3>{prediction?.predicted_category || 'No prediction yet'}</h3><p>{prediction ? `Latest prediction from ${formatDate(prediction.created_at)}.` : 'An administrator can run a prediction after an evaluation is recorded.'}</p></div><div className="confidence"><strong>{prediction ? `${(prediction.confidence_score * 100).toFixed(2)}%` : '—'}</strong><span>confidence</span></div><div className="ring"><span>{employee.score}</span><small>score</small></div></div><div className="stats-grid three"><StatCard label="Current score" value={`${employee.score} / 100`} detail="Latest evaluation" icon={BarChart3} /><StatCard label="Latest evaluation" value={formatDate(latestEvaluation?.date, { month: 'short', day: 'numeric' })} detail={latestEvaluation ? `Evaluator #${latestEvaluation.evaluator_id}` : 'No evaluation yet'} tone="blue" icon={ClipboardCheck} onClick={() => navigate(rolePath(user.role, 'evaluations'))} /><StatCard label="Attendance" value={`${employee.attendance_rate}%`} detail="Recorded attendance rate" tone="purple" icon={CheckCircle2} /></div><div className="dashboard-grid"><div className="panel chart-panel"><SectionHeader title="Your performance trend" action={<span className="subtle-label">Evaluation history</span>} /><div className="chart-wrap tall"><ResponsiveContainer width="100%" height="100%"><LineChart data={trend} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}><CartesianGrid vertical={false} stroke="#e6edf0" /><XAxis dataKey="month" axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} /><Tooltip /><Line type="monotone" dataKey="score" stroke="#0e8f88" strokeWidth={3} /></LineChart></ResponsiveContainer></div></div><div className="panel evaluation-card"><SectionHeader title="Latest evaluation" action={<NavLink className="text-button" to={rolePath(user.role, 'evaluations')}>View history <ArrowUpRight size={14} /></NavLink>} /><div className="evaluation-score"><strong>{employee.score}</strong><span>/100</span><StatusBadge status={employee.status} /></div><p>{latestEvaluation?.comments || 'No comments were included in the latest evaluation.'}</p></div></div></>
}

function ClientDashboard() {
  const resource = useApiResource(getMyClient)
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  if (!resource.data) return <QueryState loading error="Loading client account..." onRetry={resource.retry} />
  const client = resource.data
  return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry}><><div className="welcome-row"><div><p className="eyebrow">CLIENT PORTAL</p><h2>Welcome, {client.company_name}</h2><p className="muted">Your Lexie account and service information.</p></div><span className="status-badge success">{client.account_status}</span></div><div className="stats-grid three"><StatCard label="Account status" value={client.account_status} detail="Client account" icon={CheckCircle2} /><StatCard label="Services used" value={client.services_used} detail={client.company_name} tone="blue" icon={BriefcaseBusiness} /><StatCard label="Next renewal" value={client.next_renewal_date ? formatDate(client.next_renewal_date, { month: 'short', day: 'numeric' }) : 'Not scheduled'} detail={client.next_renewal_date ? formatDate(client.next_renewal_date, { year: 'numeric' }) : 'Contact your account team'} tone="purple" icon={Clock3} /></div><div className="panel service-panel"><SectionHeader title="Account information" action={<span className="subtle-label">{client.client_code}</span>} /><p><strong>{client.company_name}</strong></p><p className="muted">{client.services_used}</p><p className="muted">Renewal date: {formatDate(client.next_renewal_date)}</p></div></></QueryState>
}

function Employees() {
  const user = useAuthStore((state) => state.user)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('name')
  const [role, setRole] = useState('all')
  const [modal, setModal] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput.trim()), 350)
    return () => clearTimeout(timeout)
  }, [searchInput])
  const resource = useApiResource(async () => {
    const params = { search: search || undefined }
    const [employees, evaluations, predictions, accounts] = await Promise.all([
      getEmployees(params),
      getEvaluations(),
      getPredictions(),
      getUserAccounts(),
    ])
    return {
      employees: employees.map((employee) => presentEmployee(employee, evaluations, predictions)),
      teamLeaders: accounts.filter((account) => account.role === 'team_leader' && account.is_active),
    }
  }, [search])
  const employees = resource.data?.employees || []
  const filteredRows = employees.filter((employee) => role === 'all' || employeeRoleGroup(employee.role) === role)
  const rows = [...filteredRows].sort((a, b) => sort === 'score' ? b.score - a.score : a.name.localeCompare(b.name))
  const submit = async (payload) => {
    setSaving(true)
    setSaveError('')
    try {
      await createEmployee(payload)
      setModal(false)
      setMessage('Employee created.')
      resource.retry()
    } catch (error) {
      setSaveError(error.response?.data?.detail || 'Unable to create employee.')
    } finally {
      setSaving(false)
    }
  }
  const roleOptions = accountRoleOptions[user.role] || []
  return <><div className="page-heading"><div><p className="eyebrow">PEOPLE DIRECTORY</p><h2>Employees</h2><p className="muted">Track performance health across your organization.</p></div><button className="primary-button" onClick={() => setModal(true)}><Plus size={17} /> Add employee</button></div>{message && <div className="inline-feedback" role="status">{message}</div>}<div className="panel table-panel"><div className="table-toolbar"><div className="search-field"><Search size={17} /><input placeholder="Search employees" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} /></div><select className="secondary-button" value={sort} onChange={(event) => setSort(event.target.value)}><option value="name">Sort: name</option><option value="score">Sort: score</option></select><select className="secondary-button" value={role} onChange={(event) => setRole(event.target.value)}><option value="all">All roles</option><option value="management">Management</option><option value="team_leader">Team Leader</option><option value="staff">Staff</option></select></div><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} empty={Boolean(resource.data && rows.length === 0)} emptyMessage="Try another search or role filter."><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Role</th><th>Performance score</th><th>Prediction</th><th>Last evaluation</th><th /></tr></thead><tbody>{rows.map((employee) => <EmployeeRow key={employee.id} employee={employee} />)}</tbody></table></div></QueryState></div>{modal && <Modal title="Add employee" onClose={() => setModal(false)}><EmployeeForm roleOptions={roleOptions} teamLeaders={resource.data?.teamLeaders || []} onCancel={() => setModal(false)} onSubmit={submit} loading={saving} error={saveError} /></Modal>}</>
}

function EmployeeRow({ employee }) {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const path = rolePath(user.role, `employees/${employee.id}`)
  return <tr onClick={() => navigate(path)} className="clickable-row"><td><div className="person-cell"><span className="avatar small">{employee.avatar}</span><span><strong>{employee.name}</strong><small>{employee.employee_code} · {employee.roleLabel}</small></span></div></td><td>{employee.roleLabel}</td><td><div className="score-cell"><strong>{employee.score}</strong><div className="mini-bar"><i style={{ width: `${employee.score}%` }} /></div></div></td><td><StatusBadge status={employee.status} /></td><td>{formatDate(employee.latestEvaluation?.date)}</td><td><button className="icon-button" aria-label={`Open ${employee.name}`} onClick={(event) => { event.stopPropagation(); navigate(path) }}><ArrowUpRight size={15} /></button></td></tr>
}

function MyTeam() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const resource = useApiResource(getEmployees)
  const employees = resource.data || []
  return <><div className="page-heading"><div><p className="eyebrow">TEAM WORKSPACE</p><h2>My Team</h2><p className="muted">Employees assigned to you as Team Leader.</p></div></div><div className="panel table-panel"><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} empty={Boolean(resource.data && employees.length === 0)} emptyMessage="No employees are assigned to your team yet."><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Position</th><th>Department</th><th>Status</th><th /></tr></thead><tbody>{employees.map((employee) => <tr key={employee.id} className="clickable-row" onClick={() => navigate(rolePath(user.role, `employees/${employee.id}`))}><td><div className="person-cell"><span className="avatar small">{initials(employee.name)}</span><span><strong>{employee.name}</strong><small>{employee.employee_code}</small></span></div></td><td>{employee.position}</td><td>{employee.department}</td><td><StatusBadge status={employee.status} /></td><td><ArrowUpRight size={15} /></td></tr>)}</tbody></table></div></QueryState></div></>
}

function UserAccounts() {
  const user = useAuthStore((state) => state.user)
  const roleOptions = accountRoleOptions[user.role] || accountRoleOptions.management
  const resource = useApiResource(getUserAccounts)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editingRole, setEditingRole] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const saveAccount = async (id, changes) => {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await updateUserAccount(id, changes)
      setEditingId(null)
      setMessage('Account updated.')
      resource.retry()
    } catch (requestError) {
      const details = requestError.response?.data
      setError(details?.detail || Object.values(details || {}).flat().join(' ') || 'Unable to update account.')
    } finally {
      setSaving(false)
    }
  }

  const createAccount = async (account) => {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await createUserAccount(account)
      setCreating(false)
      setMessage('Account created.')
      resource.retry()
    } catch (requestError) {
      const details = requestError.response?.data
      setError(details?.detail || Object.values(details || {}).flat().join(' ') || 'Unable to create account.')
    } finally {
      setSaving(false)
    }
  }

  const accounts = resource.data || []
  return <><div className="page-heading"><div><p className="eyebrow">ACCESS MANAGEMENT</p><h2>User accounts</h2><p className="muted">Create accounts and manage roles and access status.</p></div><button className="primary-button" onClick={() => { setError(''); setCreating(true) }}><Plus size={17} /> Create account</button></div>{message && <div className="inline-feedback" role="status">{message}</div>}{error && !creating && <div className="form-error" role="alert">{error}</div>}<div className="panel table-panel"><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} empty={Boolean(resource.data && accounts.length === 0)} emptyMessage="No user accounts found."><div className="table-scroll"><table><thead><tr><th>Account</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{accounts.map((account) => { const editable = editingId === account.id; const role = accountRoleGroup(account.role); const self = account.id === user.id; return <tr key={account.id}><td><div className="person-cell"><span className="avatar small">{initials(account.name)}</span><span><strong>{account.name}</strong><small>{account.email}</small></span></div></td><td>{editable ? <select className="secondary-button" value={editingRole} onChange={(event) => setEditingRole(event.target.value)}>{roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : employeeRoleLabel(account.role)}</td><td><StatusBadge status={account.is_active ? 'Active' : 'Inactive'} /></td><td><div className="profile-actions">{editable ? <><button className="primary-button" disabled={saving || self} onClick={() => saveAccount(account.id, { role: editingRole })}>Save role</button><button className="secondary-button" onClick={() => setEditingId(null)}>Cancel</button></> : <><button className="secondary-button" disabled={self || account.role === 'admin'} onClick={() => { setEditingRole(roleOptions.some(([value]) => value === role) ? role : roleOptions[0][0]); setEditingId(account.id) }}>Edit role</button><button className="secondary-button" disabled={saving || self || account.role === 'admin'} onClick={() => saveAccount(account.id, { is_active: !account.is_active })}>{account.is_active ? 'Deactivate' : 'Activate'}</button></>}</div></td></tr> })}</tbody></table></div></QueryState></div>{creating && <Modal title="Create user account" onClose={() => { setCreating(false); setError('') }}><UserAccountForm roleOptions={roleOptions} onCancel={() => { setCreating(false); setError('') }} onSubmit={createAccount} loading={saving} error={error} /></Modal>}</>
}

function UserAccountForm({ roleOptions, onCancel, onSubmit, loading, error }) {
  const [values, setValues] = useState({ name: '', email: '', password: '', role: roleOptions[0]?.[0] || 'staff' })
  const update = (key, value) => setValues((current) => ({ ...current, [key]: value }))
  return <form className="modal-form" onSubmit={(event) => { event.preventDefault(); onSubmit(values) }}><label>Full name<input required maxLength="160" value={values.name} onChange={(event) => update('name', event.target.value)} /></label><label>Email<input type="email" required value={values.email} onChange={(event) => update('email', event.target.value)} /></label><label>Password<input type="password" autoComplete="new-password" minLength="8" required value={values.password} onChange={(event) => update('password', event.target.value)} /></label><label>Role<select value={values.role} onChange={(event) => update('role', event.target.value)}>{roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button type="submit" className="primary-button" disabled={loading}>{loading ? 'Creating...' : 'Create account'}</button></div></form>
}

function EmployeeForm({
  onCancel,
  onSubmit,
  loading = false,
  error = '',
  initial = {},
  roleOptions = [],
  teamLeaders = [],
  canEditRole = true,
  canEditTeamLeader = true,
}) {
  const [values, setValues] = useState({
    name: initial.name || '',
    role: initial.role || roleOptions.find(([value]) => value === 'staff')?.[0] || roleOptions[0]?.[0] || 'staff',
    team_leader_id: initial.team_leader_id ? String(initial.team_leader_id) : '',
    department: initial.department || 'Recruitment',
  })
  const update = (key, value) => setValues((current) => ({ ...current, [key]: value }))
  const submit = (event) => {
    event.preventDefault()
    onSubmit({
      ...values,
      role: values.role,
      team_leader_id: values.role === 'staff' && values.team_leader_id
        ? Number(values.team_leader_id)
        : null,
      date_hired: initial.date_hired || new Date().toISOString().slice(0, 10),
      status: initial.status || 'active',
      attendance_rate: initial.attendance_rate ?? 100,
      tenure_months: initial.tenure_months ?? 0,
      training_hours: initial.training_hours ?? 0,
    })
  }
  const editing = Boolean(initial.id)
  return <form className="modal-form" onSubmit={submit}><label>Full name<input required value={values.name} onChange={(event) => update('name', event.target.value)} placeholder="e.g. Jordan Lee" /></label><label>Role<select required disabled={!canEditRole} value={values.role} onChange={(event) => update('role', event.target.value)}>{roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{values.role === 'staff' && <label>Team Leader<select required disabled={!canEditTeamLeader} value={values.team_leader_id} onChange={(event) => update('team_leader_id', event.target.value)}><option value="">Select a Team Leader</option>{teamLeaders.map((leader) => <option key={leader.id} value={leader.id}>{leader.name}</option>)}</select></label>}<label>Department<select value={values.department} onChange={(event) => update('department', event.target.value)}>{trainedDepartments.map((item) => <option key={item}>{item}</option>)}</select></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button type="submit" className="primary-button" disabled={loading}>{loading ? 'Saving...' : editing ? 'Save changes' : 'Create employee'}</button></div></form>
}

function EmployeeProfile() {
  const { id } = useParams()
  const user = useAuthStore((state) => state.user)
  const canEdit = user.role === 'admin' || managementRoles.includes(user.role) || user.role === 'team_leader'
  const canEvaluate = managementRoles.includes(user.role) || user.role === 'team_leader'
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const resource = useApiResource(async () => {
    const employee = await getEmployee(id)
    const [evaluations, predictions, accounts] = await Promise.all([
      getEvaluations(employee.id),
      getPredictions(employee.id),
      managementRoles.includes(user.role) ? getUserAccounts() : Promise.resolve([]),
    ])
    const teamLeaders = managementRoles.includes(user.role)
      ? accounts.filter((account) => account.role === 'team_leader' && account.is_active)
      : user.role === 'team_leader'
        ? [{ id: user.id, name: user.name }]
        : []
    return {
      employee: presentEmployee(employee, evaluations, predictions),
      evaluations,
      predictions,
      teamLeaders,
    }
  }, [id])
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  if (!resource.data) return <QueryState loading error="Loading employee data..." onRetry={resource.retry} />
  const canEditRole = managementRoles.includes(user.role)
  const roleOptions = canEditRole
    ? accountRoleOptions[user.role] || []
    : [[resource.data.employee.role, employeeRoleLabel(resource.data.employee.role)]]
  const submitUpdate = async (changes) => {
    setSaving(true)
    setSaveError('')
    try {
      await updateEmployee(resource.data.employee.id, changes)
      setEditing(false)
      resource.retry()
    } catch (error) {
      setSaveError(error.response?.data?.detail || 'Unable to update employee.')
    } finally {
      setSaving(false)
    }
  }
  return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry}><><NavLink to={rolePath(user.role, user.role === 'team_leader' ? 'team' : 'employees')} className="back-link">← Back to employees</NavLink><div className="profile-heading"><div className="profile-person"><span className="avatar xl">{resource.data.employee.avatar}</span><div><p className="eyebrow">{resource.data.employee.employee_code}</p><h2>{resource.data.employee.name}</h2><p>{resource.data.employee.position} · {resource.data.employee.department}</p></div></div><div className="profile-actions">{canEdit && <button className="secondary-button" onClick={() => setEditing(true)}><Settings size={16} /> Edit profile</button>}{canEvaluate && <NavLink to={rolePath(user.role, 'evaluations')} className="primary-button"><Plus size={16} /> New evaluation</NavLink>}</div></div><div className="stats-grid three"><StatCard label="Performance score" value={`${resource.data.employee.score} / 100`} detail="Latest evaluation" icon={BarChart3} /><StatCard label="Predicted category" value={resource.data.employee.status} detail="Latest available prediction" tone="blue" icon={Sparkles} /><StatCard label="Last evaluation" value={formatDate(resource.data.employee.latestEvaluation?.date)} detail={resource.data.latestEvaluation ? `Evaluator #${resource.data.latestEvaluation.evaluator_id}` : 'No evaluation'} tone="purple" icon={ClipboardCheck} /></div><div className="dashboard-grid"><div className="panel evaluation-card"><SectionHeader title="Performance overview" action={<StatusBadge status={resource.data.employee.status} />} /><div className="evaluation-score"><strong>{resource.data.employee.score}</strong><span>/100</span></div><p>{resource.data.employee.latestEvaluation?.comments || 'No comments were included in the latest evaluation.'}</p></div><div className="panel prediction-panel"><SectionHeader title="Prediction history" action={<NavLink className="text-button" to={rolePath(user.role, 'predictions')}>Open predictions <ArrowUpRight size={14} /></NavLink>} /><p className="muted">{resource.data.predictions[0]?.predicted_category || 'No prediction has been run yet.'}</p></div></div>{editing && <Modal title="Edit employee" onClose={() => setEditing(false)}><EmployeeForm initial={resource.data.employee} roleOptions={roleOptions} teamLeaders={resource.data.teamLeaders} canEditRole={canEditRole} canEditTeamLeader={managementRoles.includes(user.role)} onCancel={() => setEditing(false)} onSubmit={submitUpdate} loading={saving} error={saveError} /></Modal>}</></QueryState>
}

function Evaluations() {
  const user = useAuthStore((state) => state.user)
  const own = staffRoles.includes(user.role)
  const [modal, setModal] = useState(false)
  const [selected, setSelected] = useState(null)
  const [editing, setEditing] = useState(false)
  const [department, setDepartment] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [hybridRating, setHybridRating] = useState('')
  const resource = useApiResource(async () => {
    const [evaluations, employees, criteria] = await Promise.all([
      getEvaluations(undefined, { department, start_date: startDate, end_date: endDate, status: statusFilter, limit: 200 }),
      getEmployees({ limit: 200 }),
      fetchEvaluationCriteria(),
    ])
    return { evaluations, employees, criteria }
  }, [department, startDate, endDate, statusFilter])
  if (resource.loading || resource.error) return <><div className="page-heading"><div><p className="eyebrow">PERFORMANCE REVIEWS</p><h2>{own ? 'My evaluations' : 'Evaluations'}</h2></div></div><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} /></>
  if (!resource.data) return <QueryState loading error="Loading evaluations..." onRetry={resource.retry} />
  const submit = async (evaluation) => {
    setSaving(true)
    setSaveError('')
    try {
      if (editing && selected) await updateEvaluation(selected.id, evaluation)
      else await createEvaluation(evaluation)
      setModal(false)
      setEditing(false)
      setSelected(null)
      resource.retry()
    } catch (error) {
      setSaveError(error.response?.data?.detail || 'Unable to create evaluation.')
    } finally {
      setSaving(false)
    }
  }
  const remove = async () => {
    if (!selected || !window.confirm('Delete this evaluation? This action cannot be undone.')) return
    try {
      await deleteEvaluation(selected.id)
      setSelected(null)
      resource.retry()
    } catch (error) {
      setSaveError(error.response?.data?.detail || 'Unable to delete evaluation.')
    }
  }
  const saveHybridRating = async (event) => {
    event.preventDefault()
    setSaveError('')
    try {
      const field = user.role === 'team_leader' ? 'team_leader_rating' : 'management_rating'
      const updated = await submitHybridReview(selected.id, { [field]: Number(hybridRating) })
      setSelected((current) => ({ ...current, ...updated }))
      setHybridRating('')
      resource.retry()
    } catch (error) {
      setSaveError(error.response?.data?.detail || 'Unable to save hybrid review.')
    }
  }
  const empty = resource.data && resource.data.evaluations.length === 0
  const canHybridReview = managementRoles.includes(user.role) || user.role === 'team_leader'
  return <><div className="page-heading"><div><p className="eyebrow">PERFORMANCE REVIEWS</p><h2>{own ? 'My evaluations' : 'Evaluations'}</h2><p className="muted">{own ? 'Review your feedback and growth signals.' : 'Create and review employee evaluations.'}</p></div>{!own && <div className="profile-actions"><NavLink className="secondary-button" to={rolePath(user.role, 'evaluation-criteria')}>Manage criteria</NavLink><button className="primary-button" onClick={() => { setEditing(false); setModal(true) }}><Plus size={17} /> Create evaluation</button></div>}</div><div className="panel settings-form"><div className="form-grid"><label>Department<select value={department} onChange={(event) => setDepartment(event.target.value)}><option value="">All departments</option>{[...new Set((resource.data?.employees || []).map((employee) => employee.department))].sort().map((name) => <option key={name}>{name}</option>)}</select></label><label>From<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>Through<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><label>Status<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option><option value="completed">Completed</option><option value="pending">Pending</option><option value="draft">Draft</option></select></label></div></div><div className="panel list-panel"><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} empty={Boolean(empty)} emptyMessage="No evaluations match these filters."><div className="list-tabs"><span className="active">Evaluations <span>{resource.data.evaluations.length}</span></span></div>{resource.data.evaluations.map((item) => <EvaluationRow key={item.id} item={item} employee={resource.data.employees.find((row) => row.id === item.employee_id)} onClick={() => { setSelected(item); setHybridRating(''); setSaveError('') }} />)}</QueryState></div>{modal && <Modal title={editing ? 'Edit evaluation' : 'Create evaluation'} onClose={() => { setModal(false); setEditing(false) }}><EvaluationForm initial={editing ? selected : null} employees={resource.data?.employees || []} criteria={resource.data?.criteria || {}} onCancel={() => { setModal(false); setEditing(false) }} onSubmit={submit} loading={saving} error={saveError} /></Modal>}{selected && <Modal title={`Evaluation · ${formatDate(selected.date)}`} onClose={() => setSelected(null)}><div className="detail-content"><p>{selected.comments || 'No comments were included.'}</p><p className="muted">Status: {selected.phase || selected.status || 'Completed'} · Hybrid score: {selected.hybrid_score ?? 'Not scored'}</p><div className="metric-list wide">{[['productivity_score', selected.productivity_score], ['attendance_score', selected.attendance_score], ['quality_score', selected.quality_score], ['teamwork_score', selected.teamwork_score], ['communication_score', selected.communication_score]].map(([key, value]) => <div key={key}><span>{resource.data?.criteria[key] || key}</span><div className="mini-bar"><i style={{ width: `${Math.max(0, Math.min(100, Number(value || 0)))}%` }} /></div><strong>{value ?? '—'}</strong></div>)}</div>{canHybridReview && <form className="modal-form" onSubmit={saveHybridRating}><label>{user.role === 'team_leader' ? 'Team Leader rating' : 'Management rating'}<input type="number" min="0" max="100" step="0.01" required value={hybridRating} onChange={(event) => setHybridRating(event.target.value)} /></label><button className="primary-button">Save hybrid review</button></form>}{saveError && <p className="form-error" role="alert">{saveError}</p>}<div className="form-actions">{[...managementRoles, 'team_leader'].includes(user.role) && <button className="secondary-button" onClick={() => { setEditing(true); setModal(true) }}>Edit evaluation</button>}{managementRoles.includes(user.role) && <button className="secondary-button" onClick={remove}>Delete evaluation</button>}</div></div></Modal>}</>
}

function EvaluationRow({ item, employee, onClick }) {
  const score = averageEvaluation(item)
  return <button className="evaluation-row evaluation-row-button" onClick={onClick}><span className="avatar small">{initials(employee?.name)}</span><div className="evaluation-row-main"><div><strong>{formatDate(item.date)}</strong><span>{employee?.name || `Employee #${item.employee_id}`} · Evaluator #{item.evaluator_id}</span></div><p>{item.comments || 'No comments provided.'}</p></div><div className="row-score"><strong>{Math.round(score)}</strong><small>/100</small></div><StatusBadge status={categoryForScore(score)} /><MoreHorizontal size={17} /></button>
}

function EvaluationForm({ employees, initial, criteria, onCancel, onSubmit, loading = false, error = '' }) {
  const [employeeId, setEmployeeId] = useState(initial?.employee_id || employees[0]?.id || '')
  const [comments, setComments] = useState(initial?.comments || '')
  const [values, setValues] = useState(() => Object.fromEntries(['productivity_score', 'attendance_score', 'quality_score', 'teamwork_score', 'communication_score'].map((field) => [field, initial?.[field] ?? 80])))
  const update = (key, value) => setValues((current) => ({ ...current, [key]: Number(value) }))
  return <form className="modal-form" onSubmit={(event) => { event.preventDefault(); onSubmit({ employee_id: Number(employeeId), date: initial?.date || new Date().toISOString().slice(0, 10), ...values, comments }) }}><label>Employee<select required value={employeeId} disabled={Boolean(initial)} onChange={(event) => setEmployeeId(event.target.value)}>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.name} · {employee.employee_code}</option>)}</select></label>{Object.entries(values).map(([key, value]) => <label className="range-field" key={key}>{criteria[key] || key.replace('_score', '').replaceAll('_', ' ')}<output>{value}</output><input type="range" min="0" max="100" value={value} onChange={(event) => update(key, event.target.value)} /></label>)}<label>Comments<textarea value={comments} onChange={(event) => setComments(event.target.value)} rows="3" /></label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button type="submit" className="primary-button" disabled={loading || employees.length === 0}>{loading ? 'Saving...' : initial ? 'Save evaluation' : 'Publish evaluation'}</button></div></form>
}

function Predictions() {
  const user = useAuthStore((state) => state.user)
  const own = staffRoles.includes(user.role)
  const [selectedEmployeeId, setSelectedEmployeeId] = useState('')
  const [selectedPrediction, setSelectedPrediction] = useState(null)
  const [running, setRunning] = useState(false)
  const [actionError, setActionError] = useState('')
  const [detail, setDetail] = useState(false)
  const resource = useApiResource(async () => {
    const [employees, predictions] = await Promise.all([getEmployees(), getPredictions()])
    return { employees, predictions }
  })
  const data = resource.data
  useEffect(() => {
    if (data?.employees.length && !selectedEmployeeId) setSelectedEmployeeId(String(data.employees[0].id))
  }, [data, selectedEmployeeId])
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  if (!data) return <QueryState loading error="Loading predictions..." onRetry={resource.retry} />
  const currentPrediction = selectedPrediction || data.predictions.find((item) => item.employee_id === Number(selectedEmployeeId))
  const employee = data?.employees.find((item) => item.id === Number(selectedEmployeeId))
  const run = async () => {
    if (!selectedEmployeeId) return
    setRunning(true)
    setActionError('')
    try {
      const prediction = await runPrediction(selectedEmployeeId)
      setSelectedPrediction(prediction)
      resource.retry()
    } catch (error) {
      setActionError(error.response?.data?.detail || 'Unable to run prediction.')
    } finally {
      setRunning(false)
    }
  }
  if (!currentPrediction) return <><div className="page-heading"><div><p className="eyebrow">MACHINE LEARNING INSIGHTS</p><h2>{own ? 'My predicted outlook' : 'Predictions'}</h2><p className="muted">Random Forest predictions based on the latest employee evaluation.</p></div>{!own && <div className="profile-actions"><select className="secondary-button" aria-label="Employee to predict" value={selectedEmployeeId} onChange={(event) => { setSelectedEmployeeId(event.target.value); setSelectedPrediction(null) }}>{data.employees.map((item) => <option key={item.id} value={item.id}>{item.name} - {item.employee_code}</option>)}</select><button className="primary-button" onClick={run} disabled={running || !selectedEmployeeId}><Sparkles size={16} /> {running ? 'Running model...' : 'Run prediction'}</button></div>}</div>{actionError && <div className="empty-state" role="alert"><p>{actionError}</p><button className="secondary-button" onClick={() => setActionError('')}>Dismiss</button></div>}<div className="empty-state"><h3>No prediction yet</h3><p>{own ? 'A prediction is not available for your employee record yet.' : `Run the model to create a prediction for ${employee?.name || 'this employee'}.`}</p></div></>
  return <><div className="page-heading"><div><p className="eyebrow">MACHINE LEARNING INSIGHTS</p><h2>{own ? 'My predicted outlook' : 'Predictions'}</h2><p className="muted">Random Forest predictions based on the latest employee evaluation.</p></div>{!own && <div className="profile-actions"><select className="secondary-button" aria-label="Employee to predict" value={selectedEmployeeId} onChange={(event) => { setSelectedEmployeeId(event.target.value); setSelectedPrediction(null) }}>{(data?.employees || []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.employee_code}</option>)}</select><button className="primary-button" onClick={run} disabled={running || resource.loading || !selectedEmployeeId}><Sparkles size={16} /> {running ? 'Running model...' : 'Run prediction'}</button></div>}</div>{actionError && <div className="empty-state" role="alert"><p>{actionError}</p><button className="secondary-button" onClick={() => setActionError('')}>Dismiss</button></div>}<QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} empty={Boolean(data && !currentPrediction)} emptyMessage={own ? 'No prediction is available for your employee record yet.' : 'No predictions yet. Select an employee and run the model.'}><div className="prediction-hero"><div><p className="eyebrow">{own ? 'YOUR OUTLOOK' : employee?.name || 'EMPLOYEE OUTLOOK'}</p><h3>{currentPrediction.predicted_category}</h3><p>Prediction based on the latest recorded evaluation.</p></div><div className="prediction-hero-stat"><strong>{(currentPrediction.confidence_score * 100).toFixed(2)}%</strong><span>confidence</span></div></div><div className="panel prediction-panel"><SectionHeader title="Feature importance" action={<button className="text-button" onClick={() => setDetail(!detail)}>{detail ? 'Hide details' : 'View details'} <ArrowUpRight size={14} /></button>} /><PredictionDetails prediction={currentPrediction} expanded={detail} /><div className="subtle-label">Generated {formatDate(currentPrediction.created_at)}</div></div></QueryState></>
}

function PredictionDetails({ prediction, expanded }) {
  const labels = { attendance_rate: 'Attendance rate', productivity_score: 'Productivity', quality_of_work: 'Quality of work', teamwork_score: 'Teamwork', communication_score: 'Communication', tenure_months: 'Tenure', training_hours: 'Training hours', department_encoded: 'Department' }
  const features = Object.entries(prediction.feature_importances || {}).sort((a, b) => b[1] - a[1])
  return <div className="prediction-detail"><div className="prediction-result"><div className="prediction-spark"><Sparkles size={22} /></div><div><StatusBadge status={prediction.predicted_category} /><h3>{(prediction.confidence_score * 100).toFixed(2)}% confidence</h3><p>Model-estimated category from current performance features.</p></div></div>{expanded && <div className="feature-list">{features.map(([name, value]) => <div key={name}><span>{labels[name] || name}</span><div className="mini-bar"><i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div><strong>{(value * 100).toFixed(2)}%</strong></div>)}</div>}</div>
}

function Reports() {
  const resource = useApiResource(async () => {
    const [summary, trend, employees, evaluations, predictions] = await Promise.all([getSummary(), getPerformanceTrend(), getEmployees(), getEvaluations(), getPredictions()])
    const performers = employees.map((employee) => presentEmployee(employee, evaluations, predictions))
    return { summary, trend, highPerformerCount: performers.filter((employee) => employee.status === 'High Performer').length }
  })
  const [type, setType] = useState('summary')
  const data = resource.data
  if (resource.loading || resource.error) return <QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry} />
  if (!data) return <QueryState loading error="Loading report data..." onRetry={resource.retry} />
  if (!data.trend.length) return <QueryState empty emptyMessage="No evaluations are available for reporting yet." />
  const exportReport = () => downloadData(`lexie-${type}-report.json`, { reportType: type, summary: data.summary, performanceTrend: data.trend, generatedAt: new Date().toISOString() })
  return <><div className="page-heading"><div><p className="eyebrow">INSIGHT CENTER</p><h2>Reports</h2><p className="muted">A concise read on performance across Lexie.</p></div><div className="profile-actions"><select className="secondary-button" value={type} onChange={(event) => setType(event.target.value)}><option value="summary">Summary report</option><option value="department">Department analytics</option><option value="trend">Performance trends</option></select><button className="primary-button" onClick={exportReport} disabled={!data}><Download size={16} /> Export report</button></div></div><QueryState loading={resource.loading} error={resource.error} onRetry={resource.retry}><><div className="stats-grid"><StatCard label="High performers" value={data.highPerformerCount} detail="Employees with latest reviews" icon={Sparkles} /><StatCard label="Average performance" value={Number(data.summary.average_score).toFixed(1)} detail="Across latest evaluations" tone="blue" icon={BarChart3} /><StatCard label="At risk" value={data.summary.at_risk_count} detail="Latest score below 70" tone="orange" icon={CircleAlert} /><StatCard label="Active employees" value={data.summary.active_employee_count} detail="Included in report" tone="purple" icon={Users} /></div><div className="dashboard-grid"><div className="panel chart-panel"><SectionHeader title="Performance trend" action={<span className="subtle-label">Monthly average</span>} /><div className="chart-wrap tall"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.trend} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="month" axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} /><Tooltip /><Line type="monotone" dataKey="score" stroke="var(--brand-primary)" strokeWidth={3} /></LineChart></ResponsiveContainer></div></div><div className="panel chart-panel"><SectionHeader title="Department performance" action={<button className="secondary-button" onClick={exportReport}><Download size={14} /> Export</button>} /><div className="chart-wrap tall"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.summary.department_breakdown} margin={{ top: 12, right: 8, left: -20, bottom: 0 }}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="name" axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} /><Tooltip /><Bar dataKey="score" radius={[4, 4, 0, 0]}>{data.summary.department_breakdown.map((department, index) => <Cell key={`${department.name}-${index}`} fill={department.score < 60 ? 'var(--status-danger)' : department.score < 75 ? 'var(--status-warning)' : 'var(--brand-primary)'} />)}</Bar></BarChart></ResponsiveContainer></div></div></div></></QueryState></>
}

function SettingsPage() {
  const user = useAuthStore((state) => state.user)
  const setUser = useAuthStore((state) => state.setUser)
  const [tab, setTab] = useState('profile')
  const [toast, setToast] = useState(null)
  const [profileValues, setProfileValues] = useState({ name: user.name, email: user.email })
  const [profileSaving, setProfileSaving] = useState(false)
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '', confirm_password: '' })
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [preferences, setPreferences] = useState({ email_evaluations: true, email_predictions: true, weekly_summary: false })
  const [savingPreference, setSavingPreference] = useState('')
  const profileResource = useApiResource(getUserProfile)
  const preferencesResource = useApiResource(getNotificationPrefs)
  useEffect(() => {
    if (profileResource.data) {
      setProfileValues({ name: profileResource.data.name, email: profileResource.data.email })
      setUser(profileResource.data)
    }
  }, [profileResource.data, setUser])
  useEffect(() => {
    if (preferencesResource.data) setPreferences(preferencesResource.data)
  }, [preferencesResource.data])

  const notify = (type, message) => {
    setToast({ type, message })
    window.setTimeout(() => setToast(null), 3500)
  }
  const saveProfile = async (event) => {
    event.preventDefault()
    setProfileSaving(true)
    try {
      const updated = await updateProfile(profileValues)
      setProfileValues({ name: updated.name, email: updated.email })
      setUser(updated)
      notify('success', 'Profile changes saved.')
    } catch (error) {
      notify('error', error.response?.data?.detail || 'Unable to save profile changes.')
    } finally {
      setProfileSaving(false)
    }
  }
  const savePassword = async (event) => {
    event.preventDefault()
    if (passwords.new_password !== passwords.confirm_password) {
      notify('error', 'New password and confirmation do not match.')
      return
    }
    if (passwords.new_password.length < 8) {
      notify('error', 'New password must be at least 8 characters.')
      return
    }
    setPasswordSaving(true)
    try {
      const { confirm_password, ...payload } = passwords
      await updatePassword(payload)
      setPasswords({ current_password: '', new_password: '', confirm_password: '' })
      notify('success', 'Password updated successfully.')
    } catch (error) {
      notify('error', error.response?.data?.detail || 'Unable to update password.')
    } finally {
      setPasswordSaving(false)
    }
  }
  const togglePreference = async (key, value) => {
    const previous = preferences
    const next = { ...previous, [key]: value }
    setPreferences(next)
    setSavingPreference(key)
    try {
      setPreferences(await updateNotificationPrefs(next))
      notify('success', 'Notification preference saved.')
    } catch (error) {
      setPreferences(previous)
      notify('error', error.response?.data?.detail || 'Unable to save notification preference.')
    } finally {
      setSavingPreference('')
    }
  }

  const tabs = [
    { id: 'profile', label: 'Profile', icon: UserRound },
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'security', label: 'Security', icon: ShieldCheck },
  ]
  const preferenceItems = [
    { key: 'email_evaluations', title: 'Evaluation updates', description: 'Email me when an evaluation is published.' },
    { key: 'email_predictions', title: 'Prediction updates', description: 'Email me when a new performance prediction is available.' },
    { key: 'weekly_summary', title: 'Weekly summary', description: 'Receive a weekly summary of performance activity.' },
  ]

  return <><div className="page-heading"><div><p className="eyebrow">WORKSPACE PREFERENCES</p><h2>Settings</h2><p className="muted">Manage your profile, notifications, and account security.</p></div></div><div className="settings-layout"><div className="settings-nav" role="tablist" aria-label="Settings"><button type="button" role="tab" aria-selected={tab === 'profile'} className={tab === 'profile' ? 'active' : ''} onClick={() => setTab('profile')}><UserRound size={16} /> Profile</button><button type="button" role="tab" aria-selected={tab === 'notifications'} className={tab === 'notifications' ? 'active' : ''} onClick={() => setTab('notifications')}><Bell size={16} /> Notifications</button><button type="button" role="tab" aria-selected={tab === 'security'} className={tab === 'security' ? 'active' : ''} onClick={() => setTab('security')}><ShieldCheck size={16} /> Security</button></div><div className="settings-tab-content">
    {tab === 'profile' && <section className="panel settings-form" role="tabpanel"><SectionHeader title="Profile information" action={<span className="subtle-label">{roleLabels[user.role]}</span>} /><QueryState loading={profileResource.loading} error={profileResource.error} onRetry={profileResource.retry}><form onSubmit={saveProfile}><ProfilePhotoControl /><div className="form-grid"><label>Full name<input required minLength="1" maxLength="160" value={profileValues.name} onChange={(event) => setProfileValues((current) => ({ ...current, name: event.target.value }))} /></label><label>Email address<input required type="email" value={profileValues.email} onChange={(event) => setProfileValues((current) => ({ ...current, email: event.target.value }))} /></label><label>Role<input value={roleLabels[user.role]} readOnly /></label></div><button type="submit" className="primary-button" disabled={profileSaving}>{profileSaving ? 'Saving...' : 'Save changes'}</button></form></QueryState></section>}
    {tab === 'security' && <><section className="panel settings-form" role="tabpanel"><SectionHeader title="Change password" action={<span className="subtle-label">Password update</span>} /><form className="security-form" onSubmit={savePassword}><label>Current password<input type="password" autoComplete="current-password" required value={passwords.current_password} onChange={(event) => setPasswords((current) => ({ ...current, current_password: event.target.value }))} /></label><label>New password<input type="password" autoComplete="new-password" minLength="8" required value={passwords.new_password} onChange={(event) => setPasswords((current) => ({ ...current, new_password: event.target.value }))} /></label><label>Confirm new password<input type="password" autoComplete="new-password" minLength="8" required value={passwords.confirm_password} onChange={(event) => setPasswords((current) => ({ ...current, confirm_password: event.target.value }))} /></label><p className="muted">Use at least 8 characters.</p><button type="submit" className="primary-button" disabled={passwordSaving}>{passwordSaving ? 'Updating...' : 'Update password'}</button></form></section><TrustedDevicesPanel /></>}
    {tab === 'notifications' && <section className="panel settings-form" role="tabpanel"><SectionHeader title="Notification preferences" action={<span className="subtle-label">Saved to your account</span>} /><QueryState loading={preferencesResource.loading} error={preferencesResource.error} onRetry={preferencesResource.retry}><div className="preference-list">{preferenceItems.map(({ key, title, description }) => <label className="preference-row" key={key}><span><strong>{title}</strong><small>{description}</small></span><input type="checkbox" role="switch" checked={Boolean(preferences[key])} disabled={Boolean(savingPreference)} onChange={(event) => togglePreference(key, event.target.checked)} aria-label={title} /></label>)}</div></QueryState></section>}
  </div></div>{toast && <div className={`toast settings-toast ${toast.type}`} role={toast.type === 'error' ? 'alert' : 'status'}>{toast.type === 'success' ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}{toast.message}<button type="button" className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={14} /></button></div>}</>
}

function RequireAuth({ children, roles }) {
  const user = useAuthStore((state) => state.user)
  if (!user) return <Navigate to="/login" replace />
  if (roles && !roles.includes(user.role)) return <Navigate to={roleHome(user.role)} replace />
  return children
}

function LegacyRoleRedirect() {
  const user = useAuthStore((state) => state.user)
  const location = useLocation()
  if (!user) return <Navigate to="/login" replace />
  const legacyRole = location.pathname.startsWith('/hr_manager') ? 'hr_manager' : 'employee'
  const suffix = location.pathname.slice(`/${legacyRole}`.length) || '/overview'
  return <Navigate to={`/${normalizeRole(legacyRole)}${suffix}`} replace />
}

function RoleRoutes({ role }) {
  const attendanceRoles = ['admin', 'management', 'hr_manager', 'team_leader', 'staff', 'employee']
  const isManager = managementRoles.includes(role)
  const isTeam = [...managementRoles, 'team_leader'].includes(role)
  return <RequireAuth roles={[role]}><Shell><Routes><Route path="overview" element={role === 'team_leader' ? <Navigate to={rolePath(role, 'team')} replace /> : <Dashboard />} />{isManager && <><Route path="employees" element={<Employees />} /><Route path="employees/:id" element={<EmployeeProfile />} /><Route path="accounts" element={<UserAccounts />} /><Route path="automation" element={<AutomationSettingsPage />} /><Route path="evaluation-criteria" element={<EvaluationCriteriaPage />} /></>}{isTeam && <Route path="reports" element={<ReportsPage />} />}{isTeam && <Route path="compose-notification" element={<NotificationComposerPage />} />}{role === 'admin' && <Route path="office-locations" element={<OfficeLocationsPage />} />}{role === 'team_leader' && <><Route path="team" element={<MyTeam />} /><Route path="employees/:id" element={<EmployeeProfile />} /></>}{attendanceRoles.includes(role) && <><Route path="attendance" element={<AttendancePage />} /><Route path="notifications" element={<NotificationsPage />} /></>}{role !== 'client' && <><Route path="submissions" element={<SubmissionsPage />} /><Route path="evaluations" element={<Evaluations />} /><Route path="evaluations/criteria" element={<EvaluationCriteriaPage />} /><Route path="predictions" element={<PredictionHistoryPage />} /></>}<Route path="settings" element={<SettingsPage />} /><Route path="*" element={<Navigate to={roleHome(role)} replace />} /></Routes></Shell></RequireAuth>
}

function AppConnected() {
  const user = useAuthStore((state) => state.user)
  const initialized = useAuthStore((state) => state.initialized)
  const initialize = useAuthStore((state) => state.initialize)
  useEffect(() => { void initialize() }, [initialize])
  if (!initialized) return <div className="login-page"><div className="login-panel"><div className="login-card" role="status">Restoring your session...</div></div></div>
  return <Routes><Route path="/login" element={user ? <Navigate to={roleHome(user.role)} replace /> : <Login />} /><Route path="/admin/*" element={<RoleRoutes role="admin" />} /><Route path="/management/*" element={<RoleRoutes role="management" />} /><Route path="/hr_manager/*" element={<LegacyRoleRedirect />} /><Route path="/team_leader/*" element={<RoleRoutes role="team_leader" />} /><Route path="/staff/*" element={<RoleRoutes role="staff" />} /><Route path="/employee/*" element={<LegacyRoleRedirect />} /><Route path="/client/*" element={<RoleRoutes role="client" />} /><Route path="*" element={<Navigate to={user ? roleHome(user.role) : '/login'} replace />} /></Routes>
}

export default AppConnected
