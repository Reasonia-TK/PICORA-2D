import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { startConnection } from "./backend/connection";
import { startJobEffects } from "./jobs/effects";
import { startJobEvents } from "./jobs/jobsStore";
import { t } from "./i18n";
import { offerRecovery, startAutosave } from "./io/autosave";
import { startCloseGuard } from "./io/documents";
import { findExample } from "./io/examples";
import { useDocument } from "./model/documentStore";
import { normalizeProject } from "./model/project";
import "./styles/app.css";

// 起動時の文書: GEC Cell、横軸 r・縦軸 z の軸対称 (保存先なし)。
const sample = findExample("gec_cell");
if (sample) {
  useDocument.getState().replace(normalizeProject(sample.data).project, null, { untitledName: t("examples.gec_cell") });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

void startConnection().then(() => startJobEvents());
startJobEffects();
void offerRecovery();
startAutosave();
startCloseGuard();
