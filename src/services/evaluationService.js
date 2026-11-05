import api from './api'

export async function getEvaluations(employeeId, filters = {}) {
  const params = {
    ...(employeeId === undefined || employeeId === null ? {} : { employee_id: employeeId }),
    ...filters,
  }
  const { data } = await api.get('/evaluations', { params })
  return data
}

export async function createEvaluation(evaluation) {
  const { data } = await api.post('/evaluations', evaluation)
  return data
}

export async function updateEvaluation(id, changes) {
  const { data } = await api.put(`/evaluations/${id}`, changes)
  return data
}

export async function deleteEvaluation(id) {
  await api.delete(`/evaluations/${id}`)
}
