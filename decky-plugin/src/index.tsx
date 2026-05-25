import {
  ButtonItem,
  definePlugin,
  PanelSection,
  PanelSectionRow,
  staticClasses,
  ToggleField,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";
import { useEffect, useState, VFC } from "react";
import { FaShareAlt } from "react-icons/fa";

interface ServiceStatus {
  service_enabled: boolean;
  service_active: boolean;
  api_reachable: boolean;
  version: string;
}

const getStatus = callable<[], ServiceStatus>("get_status");
const enableAutostart = callable<[], void>("enable_autostart");
const disableAutostart = callable<[], void>("disable_autostart");
const startService = callable<[], boolean>("start_service");
const stopService = callable<[], boolean>("stop_service");
const getUrl = callable<[], string>("get_url");

const Content: VFC = () => {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    const s = await getStatus();
    setStatus(s);
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10_000);
    return () => clearInterval(interval);
  }, []);

  const toggleAutostart = async (enabled: boolean) => {
    setLoading(true);
    try {
      if (enabled) {
        await enableAutostart();
        toaster.toast({ title: "DeckDrop", body: "Autostart aktiviert" });
      } else {
        await disableAutostart();
        toaster.toast({ title: "DeckDrop", body: "Autostart deaktiviert" });
      }
    } finally {
      await refresh();
      setLoading(false);
    }
  };

  const toggleRunning = async () => {
    setLoading(true);
    try {
      if (status?.service_active) {
        await stopService();
        toaster.toast({ title: "DeckDrop", body: "Service gestoppt" });
      } else {
        await startService();
        toaster.toast({ title: "DeckDrop", body: "Service gestartet" });
      }
    } finally {
      await refresh();
      setLoading(false);
    }
  };

  const openUI = async () => {
    const url = await getUrl();
    window.open(url, "_blank");
  };

  const isActive = status?.service_active ?? false;

  return (
    <PanelSection title="DeckDrop">
      <PanelSectionRow>
        <div style={{ color: isActive ? "#4caf50" : "#9e9e9e", fontSize: "0.85em" }}>
          {status == null
            ? "Lade…"
            : isActive
              ? `Läuft${status.version ? ` (v${status.version})` : ""}`
              : "Gestoppt"}
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField
          label="Beim Start ausführen"
          description="DeckDrop als Service beim Systemstart laden"
          checked={status?.service_enabled ?? false}
          disabled={loading}
          onChange={toggleAutostart}
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={loading} onClick={toggleRunning}>
          {isActive ? "Service stoppen" : "Service starten"}
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!isActive} onClick={openUI}>
          DeckDrop öffnen
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};

export default definePlugin(() => ({
  title: <div className={staticClasses.Title}>DeckDrop</div>,
  content: <Content />,
  icon: <FaShareAlt />,
  onDismount() {},
}));
