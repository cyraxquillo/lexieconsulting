import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeRole, normalizeUser, roleHome } from './roles.js'

test('legacy account roles normalize to canonical application roles', () => {
  assert.equal(normalizeRole('hr_manager'), 'management')
  assert.equal(normalizeRole('employee'), 'staff')
  assert.equal(normalizeRole('admin'), 'admin')
  assert.equal(normalizeUser({ id: 1, role: 'employee' }).role, 'staff')
})

test('legacy roles receive canonical route destinations', () => {
  assert.equal(roleHome('hr_manager'), '/management/attendance')
  assert.equal(roleHome('employee'), '/staff/overview')
})
