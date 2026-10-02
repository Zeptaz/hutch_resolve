import { createContext, useContext } from 'react'
import type { Language } from '@/api/types'
import { en, si, ta, type MessageKey, type Messages } from './messages'

const CATALOGS: Record<Language, Messages> = { en, si, ta }

export type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

export function makeTranslate(language: Language): Translate {
  const catalog = CATALOGS[language]
  return (key, vars) => {
    const template = catalog[key] ?? en[key]
    return vars ? template.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m)) : template
  }
}

/** Translate a key that is built from server data (e.g. an enum), falling back to `fallback` if unknown. */
export function hasMessage(key: string): key is MessageKey {
  return key in en
}

export type I18n = { language: Language; setLanguage: (language: Language) => void; t: Translate }

// Default: English, unchangeable. The agent dashboard (internal, English-only) uses this.
export const I18nContext = createContext<I18n>({ language: 'en', setLanguage: () => {}, t: makeTranslate('en') })

export function useI18n() {
  return useContext(I18nContext)
}
