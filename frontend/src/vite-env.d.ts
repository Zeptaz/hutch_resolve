/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_MODE?: 'mock' | 'live'
  /** Dev only; supply from untracked .env.local for a local test agent. */
  readonly VITE_DEV_AGENT_IDENTITY?: string
  readonly VITE_DEV_AGENT_CREDENTIAL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
