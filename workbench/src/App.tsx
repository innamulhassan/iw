import { useCallback, useEffect, useState } from "react";
import type { CatalogItem, Subject } from "./types";
import { getCatalog } from "./lib/api";
import { useInvestigation } from "./lib/useInvestigation";
import StartScreen from "./components/StartScreen";
import Workbench from "./components/Workbench";

export default function App() {
  const { state, error, busy, open, openExisting, decide, review, send, reset } = useInvestigation();
  const [view, setView] = useState<"start" | "workbench">("start");
  const [layers, setLayers] = useState<Record<string, string>>({});
  const [titles, setTitles] = useState<Record<string, string>>({});

  // small id → layer / id → title maps so the workbench header can label the incident. M2: the
  // CatalogItem.title was fetched for the start selector but DROPPED here — thread it through so
  // the PhaseStepper header carries a one-line description of what's being investigated.
  useEffect(() => {
    getCatalog()
      .then((c: CatalogItem[]) => {
        setLayers(Object.fromEntries(c.map((i) => [i.id, i.layer])));
        setTitles(Object.fromEntries(c.map((i) => [i.id, i.title])));
      })
      .catch(() => {
        setLayers({});
        setTitles({});
      });
  }, []);

  const start = useCallback(
    async (subject: Subject) => {
      setView("workbench");
      await open(subject);
    },
    [open]
  );

  const openId = useCallback(
    async (id: string) => {
      setView("workbench");
      await openExisting(id);
    },
    [openExisting]
  );

  const back = useCallback(() => {
    reset();
    setView("start");
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
      title={state.subject ? titles[state.subject.id] : undefined}
      onDecide={decide}
      onReview={review}
      onSend={send}
      onBack={back}
    />
  );
}
