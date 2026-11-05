import api from './api'

export async function getSubmissions() {
  const { data } = await api.get('/submissions')
  return data
}

export async function createSubmission({ title, description, file }) {
  const form = new FormData()
  form.append('title', title)
  form.append('description', description)
  form.append('file', file)
  const { data } = await api.post('/submissions', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function reviewSubmission(id, decision) {
  const { data } = await api.patch(`/submissions/${id}`, decision)
  return data
}

export async function downloadSubmission(id) {
  const { data } = await api.get(`/submissions/${id}/file`, {
    responseType: 'blob',
  })
  return data
}
