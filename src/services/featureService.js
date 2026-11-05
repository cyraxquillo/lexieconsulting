import api from './api'

export async function getReportRecords(filters) {
  const { data } = await api.get('/reports/records', { params: filters })
  return data
}

export async function downloadReport(format, filters) {
  const { data } = await api.get(`/reports/export.${format}`, {
    params: filters,
    responseType: 'blob',
  })
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = `lexie-performance-report.${format}`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function getAutomationSettings() {
  const { data } = await api.get('/automation/settings')
  return data
}

export async function updateAutomationSettings(changes) {
  const { data } = await api.patch('/automation/settings', changes)
  return data
}

export async function getNotificationRecipients() {
  const { data } = await api.get('/notifications/compose')
  return data
}

export async function sendNotification(notification) {
  const { data } = await api.post('/notifications/compose', notification)
  return data
}

export async function getEvaluationCriteria() {
  const { data } = await api.get('/evaluation-criteria')
  return data
}

export async function updateEvaluationCriteria(criteria) {
  const { data } = await api.put('/evaluation-criteria', criteria)
  return data
}

export async function submitHybridReview(evaluationId, review) {
  const { data } = await api.post(`/evaluations/${evaluationId}/hybrid-review`, review)
  return data
}

export async function rerunPredictions() {
  const { data } = await api.post('/predictions/rerun')
  return data
}

export async function getTrustedDevices() {
  const { data } = await api.get('/devices')
  return data
}

export async function updateTrustedDevice(deviceId, action) {
  const { data } = await api.patch(`/devices/${deviceId}`, { action })
  return data
}

export async function uploadProfilePhoto(photo) {
  const body = new FormData()
  body.append('photo', photo)
  const { data } = await api.post('/users/me/photo', body, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function getProfilePhoto() {
  const { data } = await api.get('/users/me/photo', { responseType: 'blob' })
  return URL.createObjectURL(data)
}

export async function deleteProfilePhoto() {
  await api.delete('/users/me/photo')
}
