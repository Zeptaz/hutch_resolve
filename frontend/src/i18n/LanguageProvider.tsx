import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { Language } from '@/api/types'
import { setDisplayLocale } from '@/lib/format'
import { I18nContext, makeTranslate } from './context'

const STORAGE_KEY = 'hutch-resolve.language'
const LANGUAGES: Language[] = ['en', 'si', 'ta']
const LOCALE: Record<Language, string> = { en: 'en-LK', si: 'si-LK', ta: 'ta-LK' }

function readSaved(): Language {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && (LANGUAGES as string[]).includes(saved)) return saved as Language
  } catch {
    /* storage unavailable */
  }
  return 'en'
}

/** Customer UI language. A per-device preference only; the server gets it as a hint on each turn. */
export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(readSaved)

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* preference just won't persist */
    }
  }, [])

  // Keep <html lang> and date formatting in step so screen readers and fonts pick the right script.
  setDisplayLocale(LOCALE[language])
  useEffect(() => {
    document.documentElement.lang = language
    return () => {
      document.documentElement.lang = 'en'
      setDisplayLocale(LOCALE.en)
    }
  }, [language])

  const value = useMemo(() => ({ language, setLanguage, t: makeTranslate(language) }), [language, setLanguage])
  return <I18nContext value={value}>{children}</I18nContext>
}
