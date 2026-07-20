import { useCallback, useEffect, useState } from "react";
import type { CatalogItem, Subject } from "./types";
import { getCatalog } from "./lib/api";
import { useInvestigation } from "./lib/useInvestigation";
import StartScreen from "./components/StartScreen";
import Workbench from "./components/Workbench";

export default function App() {
  const { state, error, busy, open, openExisting, decide, send, reset } = useInvestigation();
  const [view, setView] = useState<"start" | "workbench">("start");
  const [refreshKey, setRefreshKey] = useState(0);
  const [layers, setLayers] = useState<Record<string, string>>({});

  // a small id → layer map so the workbench header can label the incident's layer
  useEffect(() => {
    getCatalog()
      .then((c: CatalogItem[]) => setLayers(Object.fromEntries(c.map((i) => [i.id, i.layer]))))
      .catch(() => setLayers({}));
  }, []);

  const start = useCallback(
    async (subject: Subject) => {
      setView("workbench");
      await open(subject);
      setRefreshKey((k) => k + 1);
    },
    [open]
  );

  const openId = useCallback(
    async (id: string) => {
      setView("workbench");
      await openExisting(id);
      setRefreshKey((k) => k + 1);
    },
    [openExisting]
  );

  const back = useCallback(() => {
    reset();
    setView("start");
    setRefreshKey((k) => k + 1);
  }, [reset]);

  if (view === "start") {
    return <StartScreen onStart={start} onOpenExisting={openId} error={error} busy={busy} />;
  }

  return (
    <Workbench
      live={state}
      busy={busy}
      error={error}
      layer={state.subject ? layers[state.subject.id] : undefined}
      refreshKey={refreshKey}
      onDecide={decide}
      onSend={send}
      onBack={back}
      onOpenExisting={openId}
    />
  );
}
