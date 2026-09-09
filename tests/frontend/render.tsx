import type { ReactElement } from "react";
import { render as renderRTL } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import { I18nProvider } from "../../apps/web/src/i18n";
export function render(ui: ReactElement, options?: RenderOptions) {
  return renderRTL(ui, {wrapper: ({children}) => <I18nProvider initialLocale="en-US">{children}</I18nProvider>, ...options});
}
