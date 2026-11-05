import api from './api'

export async function getAttendance() {
  const { data } = await api.get('/attendance')
  return data
}

export async function getAttendanceRoster() {
  const { data } = await api.get('/attendance/roster')
  return data
}

export async function timeIn(location) {
  const { data } = await api.post('/attendance/time-in', location)
  return data
}

export async function timeOut(location) {
  const { data } = await api.post('/attendance/time-out', location)
  return data
}

export async function breakStart() {
  const { data } = await api.post('/attendance/break/start')
  return data
}

export async function breakEnd() {
  const { data } = await api.post('/attendance/break/end')
  return data
}

export async function getAttendanceCorrectionRequests() {
  const { data } = await api.get('/attendance/corrections')
  return data
}

export async function requestAttendanceCorrection(payload) {
  const { data } = await api.post('/attendance/corrections', payload)
  return data
}

export async function reviewAttendanceCorrection(id, decision) {
  const { data } = await api.patch(`/attendance/corrections/${id}`, decision)
  return data
}

export async function getOfficeLocations() {
  const { data } = await api.get('/office-locations')
  return data
}

export async function createOfficeLocation(location) {
  const { data } = await api.post('/office-locations', location)
  return data
}

export async function updateOfficeLocation(id, location) {
  const { data } = await api.patch(`/office-locations/${id}`, location)
  return data
}

export async function deleteOfficeLocation(id) {
  await api.delete(`/office-locations/${id}`)
}

export async function getNotifications() {
  const { data } = await api.get('/notifications')
  return data
}

export async function markNotificationRead(id) {
  const { data } = await api.post(`/notifications/${id}/read`)
  return data
}
