import { Suspense } from 'react'
import { createBrowserRouter, Outlet, RouterProvider, ScrollRestoration } from 'react-router'
import { EmptyState, LoadingState } from '@/components/states'
import { loadAgentPage, loadCustomerPage, loadLandingPage } from '@/routes/pages'

// Route-level lazy loading: the router fetches the next page before switching, so a link never flashes a blank page.
const page = (load: () => Promise<{ default: React.ComponentType }>) => async () => ({ Component: (await load()).default })

function Root() {
  return (
    <>
      <ScrollRestoration />
      <Outlet />
    </>
  )
}

/** Shown only while the first page's code loads. The landing page starts on the sand of its intro. */
function FirstLoad() {
  return window.location.pathname === '/' ? <div className="min-h-dvh bg-[#f6f4f2]" /> : <LoadingState />
}

const router = createBrowserRouter([
  {
    element: <Root />,
    HydrateFallback: FirstLoad,
    children: [
      { path: '/', lazy: page(loadLandingPage) },
      { path: '/chat', lazy: page(loadCustomerPage) },
      {
        path: '/agent',
        lazy: page(loadAgentPage),
        children: [
          { index: true, lazy: async () => ({ Component: (await import('@/routes/agent/CaseDetail')).NoCaseSelected }) },
          { path: 'cases/:caseId', lazy: async () => ({ Component: (await import('@/routes/agent/CaseDetail')).CaseDetailRoute }) },
        ],
      },
      { path: '*', element: <EmptyState title="Page not found" description="Check the address and try again." /> },
    ],
  },
])

export default function App() {
  return (
    <Suspense fallback={<LoadingState />}>
      <RouterProvider router={router} />
    </Suspense>
  )
}
