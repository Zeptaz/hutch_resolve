// Page chunks, shared by the router and by the landing page, which warms them up so its links open instantly.
// Customer and agent areas stay split so neither loads the other's code or session.
export const loadLandingPage = () => import('@/routes/landing/LandingPage')
export const loadCustomerPage = () => import('@/routes/customer/CustomerPage')
export const loadAgentPage = () => import('@/routes/agent/AgentPage')
