import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { I18nProvider } from "./i18n";
import "./styles.css";

/**
 * Font preferences may be seeded from the URL. The module is pulled in with a
 * dynamic import so the value is stored *before* the workspace store reads its
 * preferences during import, which keeps the first paint at the right size.
 */
async function bootstrap() {
  const { applyFontQuery, persistPreferences, readPreferences, preferenceDefaults } = await import("@localnote/workspace");
  const seeded = applyFontQuery(localStorage.getItem("localnote-preferences") ? readPreferences() : { ...preferenceDefaults }, window.location.search);
  persistPreferences(seeded);

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <I18nProvider>
        <App />
      </I18nProvider>
    </React.StrictMode>,
  );
}

void bootstrap();
