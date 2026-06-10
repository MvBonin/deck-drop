import { callable, definePlugin, openURL } from "@decky/api";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { useState, useEffect } from "react";

const start = callable<[], { success: boolean }>("start");
const stop = callable<[], { success: boolean }>("stop");
const isRunning = callable<[], { running: boolean }>("is_running");

function Content() {
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    const r = await isRunning();
    setRunning(r.running);
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleToggle = async (on: boolean) => {
    setLoading(true);
    try {
      if (on) {
        await start();
      } else {
        await stop();
      }
      // wait briefly for the process to start/stop
      await new Promise((r) => setTimeout(r, 1500));
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  return (
    <PanelSection title="DeckDrop">
      <PanelSectionRow>
        <ToggleField
          label="Im Hintergrund laufen"
          description={running ? "Aktiv auf Port 7373" : "Gestoppt"}
          checked={running}
          disabled={loading}
          onChange={handleToggle}
        />
      </PanelSectionRow>
      {running && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => openURL("http://localhost:7373")}
          >
            DeckDrop öffnen
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  title: <div className={staticClasses.Title}>DeckDrop</div>,
  content: <Content />,
  icon: <span>📦</span>,
  onDismount() {},
}));
