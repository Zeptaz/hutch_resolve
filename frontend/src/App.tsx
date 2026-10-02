import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { EmptyState, LoadingState } from '@/components/states'

// Customer and agent areas are split so neither loads the other's code or session.
const CustomerPage = lazy(() => import('@/routes/customer/CustomerPage'))
const AgentPage = lazy(() => import('@/routes/agent/AgentPage'))

const router = createBrowserRouter([
  { path: '/', element: <CustomerPage /> },
  {
    path: '/agent',
    element: <AgentPage />,
    children: [
      { index: true, lazy: async () => ({ Component: (await import('@/routes/agent/CaseDetail')).NoCaseSelected }) },
      { path: 'cases/:caseId', lazy: async () => ({ Component: (await import('@/routes/agent/CaseDetail')).CaseDetailRoute }) },
    ],
  },
  { path: '*', element: <EmptyState title="Page not found" description="Check the address and try again." /> },
])

export default function App() {
  return (
    <Suspense fallback={<LoadingState />}>
      <RouterProvider router={router} />
    </Suspense>
  )
}
