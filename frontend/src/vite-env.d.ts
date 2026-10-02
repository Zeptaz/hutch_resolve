/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_MODE?: 'mock' | 'live'
  /** Dev only, from an untracked .env.local: one-click sign-in for the local test agent. */
  readonly VITE_DEV_AGENT_IDENTITY?: string
  readonly VITE_DEV_AGENT_CREDENTIAL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
