/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_MODE?: 'mock' | 'live'
  /** Dev only; supply from untracked .env.local for a local test agent. */
  readonly VITE_DEV_AGENT_IDENTITY?: string
  readonly VITE_DEV_AGENT_CREDENTIAL?: string
  /** Optional external ticket system shown on the dashboard, e.g. "HubSpot". */
  readonly VITE_CRM_NAME?: string
  /** Optional HTTPS ticket link template; {id} is replaced by the provider ticket ID. */
  readonly VITE_CRM_TICKET_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
