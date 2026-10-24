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
  language: string;
}

// Follows the language chosen in DeckDrop (GET /api/status). Adding a
// language: add a block with the same keys as `en`.
const STRINGS = {
  en: {
    autostartOn: "Autostart enabled",
    autostartOff: "Autostart disabled",
    serviceStopped: "Service stopped",
    serviceStarted: "Service started",
    startFailed: "Failed to start – is DeckDrop installed?",
    loading: "Loading…",
    notInstalled: "⚠ Not installed",
    installHint:
      "Install DeckDrop via pipx, AppImage or Flatpak and start the app once to set up the service.",
    running: "Running",
    stopped: "Stopped",
    updatesAvailable: "Updates available:",
    autostart: "Autostart",
    autostartDesc: "Start automatically at system startup",
    stopService: "Stop service",
    startService: "Start service",
    open: "Open DeckDrop ↗",
  },
  de: {
    autostartOn: "Autostart aktiviert",
    autostartOff: "Autostart deaktiviert",
    serviceStopped: "Service gestoppt",
    serviceStarted: "Service gestartet",
    startFailed: "Fehler beim Starten – ist DeckDrop installiert?",
    loading: "Lade…",
    notInstalled: "⚠ Nicht installiert",
    installHint:
      "Installiere DeckDrop über pipx, AppImage oder Flatpak und starte die App einmal, um den Service einzurichten.",
    running: "Läuft",
    stopped: "Gestoppt",
    updatesAvailable: "Updates verfügbar:",
    autostart: "Autostart",
    autostartDesc: "Beim Systemstart automatisch starten",
    stopService: "Service stoppen",
    startService: "Service starten",
    open: "DeckDrop öffnen ↗",
  },
};
type Lang = keyof typeof STRINGS;
type Key = keyof (typeof STRINGS)["en"];

// Remembered so the panel stays in the user's language while DeckDrop is stopped.
let lastLanguage: Lang = "en";

function useT(status: Status | null): (key: Key) => string {
  if (status?.language && status.language in STRINGS) lastLanguage = status.language as Lang;
  const table = STRINGS[lastLanguage];
  return (key) => table[key] ?? STRINGS.en[key];
}

const getStatus = callable<[], Status>("get_status");
const enableAutostart = callable<[], Record<string, unknown>>("enable_autostart");
const disableAutostart = callable<[], Record<string, unknown>>("disable_autostart");
const startService = callable<[], boolean>("start_service");
const stopService = callable<[], boolean>("stop_service");
const getUpdateCount = callable<[], number>("get_update_count");

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
  const [updateCount, setUpdateCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const t = useT(status);

  const refresh = useCallback(async () => {
    try {
      const s = await getStatus();
      setStatus(s);
      setUpdateCount(s.api_reachable ? await getUpdateCount() : 0);
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
          toaster.toast({ title: "DeckDrop", body: t("autostartOn") });
        }
      } else {
        await disableAutostart();
        toaster.toast({ title: "DeckDrop", body: t("autostartOff") });
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
        toaster.toast({ title: "DeckDrop", body: t("serviceStopped") });
      } else {
        const ok = await startService();
        toaster.toast({
          title: "DeckDrop",
          body: ok ? t("serviceStarted") : t("startFailed"),
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
          <span style={{ color: "#9e9e9e", fontSize: "0.85em" }}>{t("loading")}</span>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ── Not installed ──────────────────────────────────────────────────────────
  if (!status.installed) {
    return (
      <PanelSection title="DeckDrop">
        <PanelSectionRow>
          <span style={{ color: "#ff9800", fontWeight: 500 }}>{t("notInstalled")}</span>
        </PanelSectionRow>
        <PanelSectionRow>
          <span style={{ fontSize: "0.8em", color: "#9e9e9e", lineHeight: 1.5 }}>
            {t("installHint")}
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
            {service_active ? `${t("running")}${version ? ` (v${version})` : ""}` : t("stopped")}
          </span>
        </div>
      </PanelSectionRow>

      {/* Updates available */}
      {updateCount > 0 && (
        <PanelSectionRow>
          <span style={{ color: "#4caf50", fontWeight: 500, fontSize: "0.85em" }}>
            {t("updatesAvailable")} {updateCount}
          </span>
        </PanelSectionRow>
      )}

      {/* Autostart toggle */}
      <PanelSectionRow>
        <ToggleField
          label={t("autostart")}
          description={t("autostartDesc")}
          checked={service_enabled}
          disabled={busy}
          onChange={(v) => void handleAutostart(v)}
        />
      </PanelSectionRow>

      {/* Start / Stop */}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => void handleToggleRunning()}>
          {service_active ? t("stopService") : t("startService")}
        </ButtonItem>
      </PanelSectionRow>

      {/* Open in Steam browser */}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!service_active || busy} onClick={handleOpen}>
          {t("open")}
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
