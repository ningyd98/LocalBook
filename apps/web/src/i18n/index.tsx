import { createContext, useContext, useState, ReactNode } from "react";
import { zhCN, TranslationKeys } from "./zh-CN";
import { localizedError } from "./errors";
import { enUS } from "./en-US";

export type Locale = "zh-CN" | "en-US";

const translations: Record<Locale, TranslationKeys> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: TranslationKeys;
  tr: (zh: string, en: string) => string;
  errorText: (error: unknown) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children, initialLocale }: { children: ReactNode; initialLocale?: Locale }) {
  // 默认使用中文
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (initialLocale) return initialLocale;
    let saved: string | null = null;
    try { saved = localStorage.getItem("localnote-locale"); } catch { /* storage may be unavailable */ }
    return (saved === "zh-CN" || saved === "en-US") ? saved : "zh-CN";
  });

  const setLocale = (newLocale: Locale) => {
    setLocaleState(newLocale);
    try { localStorage.setItem("localnote-locale", newLocale); } catch { /* allow in-memory preferences */ }
  };

  const value: I18nContextValue = {
    locale,
    setLocale,
    t: translations[locale],
    errorText: error => localizedError(error, locale),
    tr: (zh, en) => locale === "zh-CN" ? zh : en,
  };

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
