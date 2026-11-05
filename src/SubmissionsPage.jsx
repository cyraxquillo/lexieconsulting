import { useEffect, useState } from 'react'
import { Download, FileUp, Plus } from 'lucide-react'
import { downloadSubmission, createSubmission, getSubmissions, reviewSubmission } from './services/submissionService'
import { useAuthStore } from './store/useAuthStore'
import StatusBadge from './components/StatusBadge'

function useSubmissions() {
  const [resource, setResource] = useState({ data: [], loading: true, error: '' })
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let active = true
    setResource((current) => ({ ...current, loading: true, error: '' }))
    getSubmissions().then((data) => {
      if (active) setResource({ data, loading: false, error: '' })
    }).catch((error) => {
      if (active) setResource({
        data: [],
        loading: false,
        error: error.response?.data?.detail || error.message || 'Unable to load submissions.',
      })
    })
    return () => { active = false }
  }, [version])
  return { ...resource, retry: () => setVersion((current) => current + 1) }
}

function errorText(error) {
  const data = error.response?.data
  if (data?.detail) return data.detail
  if (data && typeof data === 'object') return Object.values(data).flat().join(' ')
  return error.message || 'The request could not be completed.'
}

function submissionStatus(status) {
  if (status === 'reviewed') return 'Approved'
  if (status === 'rejected') return 'Rejected'
  if (status === 'under_review') return 'Under review'
  return 'Pending'
}

export default function SubmissionsPage() {
  const user = useAuthStore((state) => state.user)
  const resource = useSubmissions()
  const employee = ['staff', 'employee'].includes(user.role)
  const reviewer = ['admin', 'management', 'hr_manager', 'team_leader'].includes(user.role)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [comments, setComments] = useState({})
  const [downloadError, setDownloadError] = useState('')

  const upload = async (event) => {
    event.preventDefault()
    if (!file) return
    setBusy('upload')
    setError('')
    try {
      await createSubmission({ title, description, file })
      setTitle('')
      setDescription('')
      setFile(null)
      setUploadOpen(false)
      setNotice('Work submitted for review.')
      resource.retry()
    } catch (requestError) {
      setError(errorText(requestError))
    } finally {
      setBusy('')
    }
  }

  const review = async (submission, status) => {
    setBusy(`review-${submission.id}`)
    setError('')
    try {
      await reviewSubmission(submission.id, {
        status,
        reviewer_comment: comments[submission.id] || '',
      })
      setNotice(status === 'reviewed' ? 'Submission approved.' : 'Submission rejected.')
      resource.retry()
    } catch (requestError) {
      setError(errorText(requestError))
    } finally {
      setBusy('')
    }
  }

  const download = async (submission) => {
    setDownloadError('')
    try {
      const data = await downloadSubmission(submission.id)
      const objectUrl = URL.createObjectURL(data)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = submission.file_name || 'submission'
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
    } catch (requestError) {
      setDownloadError(errorText(requestError))
    }
  }

  const submissions = resource.data
  return <><div className="page-heading"><div><p className="eyebrow">WORKSPACE</p><h2>{reviewer ? 'Work submissions' : 'My work'}</h2><p className="muted">{reviewer ? 'Review submitted work for your permitted scope.' : 'Upload work and track its review status.'}</p></div>{employee && <button className="primary-button" onClick={() => { setError(''); setUploadOpen(true) }}><Plus size={17} /> Upload work</button>}</div>{notice && <div className="inline-feedback" role="status">{notice}</div>}{error && !uploadOpen && <p className="form-error" role="alert">{error}</p>}{downloadError && <p className="form-error" role="alert">{downloadError}</p>}
    <section className="panel table-panel"><div className="table-scroll"><table><thead><tr><th>Title</th>{reviewer && <th>Employee</th>}<th>Uploaded</th><th>Status</th><th>File</th>{reviewer && <th>Review</th>}</tr></thead><tbody>
      {resource.loading && <tr><td colSpan={reviewer ? 6 : 4}>Loading submissions…</td></tr>}
      {resource.error && <tr><td colSpan={reviewer ? 6 : 4}><span role="alert">{resource.error}</span> <button className="secondary-button" onClick={resource.retry}>Try again</button></td></tr>}
      {!resource.loading && !resource.error && submissions.length === 0 && <tr><td colSpan={reviewer ? 6 : 4} className="muted">No work submissions yet.</td></tr>}
      {!resource.loading && !resource.error && submissions.map((submission) => <tr key={submission.id}>
        <td><strong>{submission.title}</strong>{submission.description && <small className="muted">{submission.description}</small>}{submission.reviewer_comment && <small className="muted">Review: {submission.reviewer_comment}</small>}</td>
        {reviewer && <td>{submission.employee_name}</td>}
        <td>{new Date(submission.uploaded_at).toLocaleString()}</td>
        <td><StatusBadge status={submissionStatus(submission.status)} label={submissionStatus(submission.status)} />{submission.is_late && <small className="muted">Late upload</small>}</td>
        <td><button className="text-button" onClick={() => download(submission)}><Download size={15} /> {submission.file_name || 'Download'}</button></td>
        {reviewer && <td>{['pending', 'under_review'].includes(submission.status) ? <div className="submission-review-controls"><textarea rows="2" placeholder="Review comment" value={comments[submission.id] || ''} onChange={(event) => setComments((current) => ({ ...current, [submission.id]: event.target.value }))} /><div className="profile-actions"><button className="primary-button" disabled={Boolean(busy)} onClick={() => review(submission, 'reviewed')}>{busy === `review-${submission.id}` ? 'Saving…' : 'Approve'}</button><button className="secondary-button" disabled={Boolean(busy)} onClick={() => review(submission, 'rejected')}>Reject</button></div></div> : '—'}</td>}
      </tr>)}
    </tbody></table></div></section>
    {uploadOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setUploadOpen(false)}><div className="modal" role="dialog" aria-modal="true"><div className="modal-header"><h2>Upload work</h2><button className="icon-button" onClick={() => setUploadOpen(false)} aria-label="Close">×</button></div><form className="modal-form" onSubmit={upload}><label>Title<input required maxLength="160" value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>Description<textarea rows="3" maxLength="5000" value={description} onChange={(event) => setDescription(event.target.value)} /></label><label>File (up to 4 MB)<input type="file" required accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.png,.jpg,.jpeg" onChange={(event) => setFile(event.target.files?.[0] || null)} />{file && <small className="muted">{file.name} · {(file.size / 1024 / 1024).toFixed(2)} MB</small>}</label>{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-button" onClick={() => setUploadOpen(false)}>Cancel</button><button type="submit" className="primary-button" disabled={busy === 'upload' || !file}><FileUp size={16} /> {busy === 'upload' ? 'Uploading…' : 'Submit work'}</button></div></form></div></div>}
  </>
}
