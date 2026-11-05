const ROLE_ALIASES = { hr_manager: 'management', employee: 'staff' }

export const normalizeRole = (role) => ROLE_ALIASES[role] || role
export const normalizeUser = (user) => user ? { ...user, role: normalizeRole(user.role) } : user

export const roleHome = (role) => {
  const normalizedRole = normalizeRole(role)
  return `/${normalizedRole}/${normalizedRole === 'team_leader' ? 'team' : normalizedRole === 'management' ? 'attendance' : 'overview'}`
}
