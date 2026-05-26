import {
  ButtonItem,
  definePlugin,
  Navigation,
  PanelSection,
  PanelSectionRow,
  staticClasses,
  ToggleField,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";
import { FC, useCallback, useEffect, useState } from "react";
import { FaShare } from "react-icons/fa6";

interface Status {
  installed: boolean;
  service_enabled: boolean;
  service_active: boolean;
  api_reachable: boolean;
  version: string;
  url: string;
}

const getStatus = callable<[], Status>("get_status");
const enableAutostart = callable<[], Record<string, unknown>>("enable_autostart");
const disableAutostart = callable<[], Record<string, unknown>>("disable_autostart");
const startService = callable<[], boolean>("start_service");
const stopService = callable<[], boolean>("stop_service");

const StatusDot: FC<{ active: boolean }> = ({ active }) => (
  <span
    style={{
      display: "inline-block",
      width: 8,
      height: 8,
      borderRadius: "50%",
      background: active ? "#4caf50" : "#757575",
      marginRight: 6,
      flexShrink: 0,
    }}
  />
);

const Content: FC = () => {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getStatus());
    } catch (_) {
      // backend not ready yet
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 10_000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleAutostart = async (enabled: boolean) => {
    setBusy(true);
    try {
      if (enabled) {
        const res = await enableAutostart();
        if (res?.error) {
          toaster.toast({ title: "DeckDrop", body: String(res.error) });
        } else {
          toaster.toast({ title: "DeckDrop", body: "Autostart aktiviert" });
        }
      } else {
        await disableAutostart();
        toaster.toast({ title: "DeckDrop", body: "Autostart deaktiviert" });
      }
    } finally {
      await refresh();
      setBusy(false);
    }
  };

  const handleToggleRunning = async () => {
    setBusy(true);
    try {
      if (status?.service_active) {
        await stopService();
        toaster.toast({ title: "DeckDrop", body: "Service gestoppt" });
      } else {
        const ok = await startService();
        toaster.toast({
          title: "DeckDrop",
          body: ok ? "Service gestartet" : "Fehler beim Starten – ist DeckDrop installiert?",
        });
      }
    } finally {
      await refresh();
      setBusy(false);
    }
  };

  const handleOpen = () => {
    Navigation.NavigateToExternalWeb(status?.url ?? "http://localhost:7373");
  };

  // ── Loading ────────────────────────────────────────────────────────────────
  if (status === null) {
    return (
      <PanelSection title="DeckDrop">
        <PanelSectionRow>
          <span style={{ color: "#9e9e9e", fontSize: "0.85em" }}>Lade…</span>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ── Not installed ──────────────────────────────────────────────────────────
  if (!status.installed) {
    return (
      <PanelSection title="DeckDrop">
        <PanelSectionRow>
          <span style={{ color: "#ff9800", fontWeight: 500 }}>⚠ Nicht installiert</span>
        </PanelSectionRow>
        <PanelSectionRow>
          <span style={{ fontSize: "0.8em", color: "#9e9e9e", lineHeight: 1.5 }}>
            Installiere DeckDrop über pipx, AppImage oder Flatpak und starte die App einmal,
            um den Service einzurichten.
          </span>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const { service_active, service_enabled, version } = status;

  // ── Normal ─────────────────────────────────────────────────────────────────
  return (
    <PanelSection title="DeckDrop">
      {/* Status */}
      <PanelSectionRow>
        <div style={{ display: "flex", alignItems: "center", fontSize: "0.85em" }}>
          <StatusDot active={service_active} />
          <span style={{ color: service_active ? "#4caf50" : "#9e9e9e" }}>
            {service_active ? `Läuft${version ? ` (v${version})` : ""}` : "Gestoppt"}
          </span>
        </div>
      </PanelSectionRow>

      {/* Autostart toggle */}
      <PanelSectionRow>
        <ToggleField
          label="Autostart"
          description="Beim Systemstart automatisch starten"
          checked={service_enabled}
          disabled={busy}
          onChange={(v) => void handleAutostart(v)}
        />
      </PanelSectionRow>

      {/* Start / Stop */}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => void handleToggleRunning()}>
          {service_active ? "Service stoppen" : "Service starten"}
        </ButtonItem>
      </PanelSectionRow>

      {/* Open in Steam browser */}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!service_active || busy} onClick={handleOpen}>
          DeckDrop öffnen ↗
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};

export default definePlugin(() => ({
  title: <div className={staticClasses.Title}>DeckDrop</div>,
  content: <Content />,
  icon: <FaShare />,
  onDismount() {},
}));
