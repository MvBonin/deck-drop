import {
  ButtonItem,
  definePlugin,
  Field,
  Navigation,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  staticClasses,
  ToggleField,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";
import { FC, useCallback, useEffect, useState } from "react";
import { FaShare } from "react-icons/fa6";

interface DownloadInfo {
  id: string;
  name: string;
  status: string;
  progress: number;
  speed: number;
  kind: string;
  source_state: string;
}

interface Status {
  installed: boolean;
  appimage: string;
  appimage_installed: boolean;
  service_installed: boolean;
  service_enabled: boolean;
  service_active: boolean;
  api_reachable: boolean;
  version: string;
  language: string;
  url: string;
  downloads: DownloadInfo[];
  peers_online: number;
  update_count: number;
  shortcut_app_id: number;
}

interface CliResult {
  error?: string;
  ok?: boolean;
  [key: string]: unknown;
}

// Follows the language chosen in DeckDrop (GET /api/status). Adding a
// language: add a block with the same keys as `en`.
const STRINGS = {
  en: {
    loading: "Loading…",
    notInstalled: "DeckDrop not found",
    installHint:
      "Download the DeckDrop AppImage in Desktop Mode (see the README) and start it once. This panel finds it in your home folder.",
    running: "Running",
    stopped: "Stopped",
    runningApp: "Running (not as background service)",
    peers: "Devices online",
    updatesAvailable: "Game updates available",
    downloads: "Transfers",
    noDownloads: "No active transfers",
    waitingHost: "Host changed the game – waiting",
    newVersion: "New version available – open DeckDrop",
    open: "Open DeckDrop",
    openDesc: "In Steam's browser – works without anything else installed",
    launchApp: "Start DeckDrop app",
    setup: "Setup",
    background: "Run in the background",
    backgroundDesc: "Starts with the Deck; keeps sharing and downloading in Gaming Mode",
    backgroundOn: "Background service enabled",
    backgroundOff: "Background service disabled",
    startService: "Start DeckDrop",
    stopService: "Stop DeckDrop",
    serviceStarted: "DeckDrop started",
    serviceStopped: "DeckDrop stopped",
    startFailed: "Could not start DeckDrop",
    install: "Install AppImage",
    installDesc: "Copies it to ~/Applications/DeckDrop.AppImage and adds menu entries – recommended",
    installed: "AppImage installed",
    library: "Show in Steam library",
    libraryDesc: "Adds “DeckDrop” as a fullscreen app (non-Steam game)",
    libraryAdded: "Added to your library",
    libraryRemoved: "Removed from your library",
    noBrowser:
      "The fullscreen app needs Chromium (Desktop Mode → Discover → Chromium). “Open DeckDrop” works without it.",
    failed: "Failed: {error}",
  },
  de: {
    loading: "Lade…",
    notInstalled: "DeckDrop nicht gefunden",
    installHint:
      "Lade das DeckDrop-AppImage im Desktop-Modus herunter (siehe README) und starte es einmal. Dieses Panel findet es in deinem Home-Ordner.",
    running: "Läuft",
    stopped: "Gestoppt",
    runningApp: "Läuft (nicht als Hintergrund-Service)",
    peers: "Geräte online",
    updatesAvailable: "Spiel-Updates verfügbar",
    downloads: "Übertragungen",
    noDownloads: "Keine aktiven Übertragungen",
    waitingHost: "Host hat das Spiel verändert – wartet",
    newVersion: "Neue Version verfügbar – DeckDrop öffnen",
    open: "DeckDrop öffnen",
    openDesc: "Im Steam-Browser – braucht sonst nichts",
    launchApp: "DeckDrop-App starten",
    setup: "Einrichtung",
    background: "Im Hintergrund laufen",
    backgroundDesc: "Startet mit dem Deck; teilt und lädt auch im Gaming Mode weiter",
    backgroundOn: "Hintergrund-Service aktiviert",
    backgroundOff: "Hintergrund-Service deaktiviert",
    startService: "DeckDrop starten",
    stopService: "DeckDrop stoppen",
    serviceStarted: "DeckDrop gestartet",
    serviceStopped: "DeckDrop gestoppt",
    startFailed: "DeckDrop konnte nicht gestartet werden",
    install: "AppImage installieren",
    installDesc: "Kopiert es nach ~/Applications/DeckDrop.AppImage und legt Menüeinträge an – empfohlen",
    installed: "AppImage installiert",
    library: "In der Steam-Bibliothek zeigen",
    libraryDesc: "Fügt „DeckDrop“ als Vollbild-App hinzu (Nicht-Steam-Spiel)",
    libraryAdded: "Zur Bibliothek hinzugefügt",
    libraryRemoved: "Aus der Bibliothek entfernt",
    noBrowser:
      "Die Vollbild-App braucht Chromium (Desktop-Modus → Discover → Chromium). „DeckDrop öffnen“ geht auch ohne.",
    failed: "Fehlgeschlagen: {error}",
  },
};
type Lang = keyof typeof STRINGS;
type Key = keyof (typeof STRINGS)["en"];

// Remembered so the panel stays in the user's language while DeckDrop is stopped.
let lastLanguage: Lang = "en";

function useT(status: Status | null): (key: Key, vars?: Record<string, string>) => string {
  if (status?.language && status.language in STRINGS) lastLanguage = status.language as Lang;
  const table = STRINGS[lastLanguage];
  return (key, vars) => {
    let text: string = table[key] ?? STRINGS.en[key];
    for (const [k, v] of Object.entries(vars ?? {})) text = text.replace(`{${k}}`, v);
    return text;
  };
}

const getStatus = callable<[], Status>("get_status");
const browserAvailable = callable<[], boolean>("browser_available");
const installApp = callable<[], CliResult>("install_app");
const setAutostart = callable<[boolean], CliResult>("set_autostart");
const startService = callable<[], CliResult>("start_service");
const stopService = callable<[], CliResult>("stop_service");
const getLaunchTarget = callable<[], { exe?: string; dir?: string; options?: string; error?: string }>(
  "get_launch_target",
);
const setShortcutAppId = callable<[number], void>("set_shortcut_app_id");

const toast = (body: string) => toaster.toast({ title: "DeckDrop", body });

function fmtSpeed(bps: number): string {
  if (!bps) return "";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bps) / Math.log(1024)));
  return `${(bps / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

function shortcutExists(appId: number): boolean {
  if (!appId) return false;
  try {
    return !!(window as any).appStore?.GetAppOverviewByAppID(appId);
  } catch {
    return false;
  }
}

const StatusDot: FC<{ color: string }> = ({ color }) => (
  <span
    style={{
      display: "inline-block",
      width: 8,
      height: 8,
      borderRadius: "50%",
      background: color,
      marginRight: 6,
      flexShrink: 0,
    }}
  />
);

const Content: FC = () => {
  const [status, setStatus] = useState<Status | null>(null);
  const [hasBrowser, setHasBrowser] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const t = useT(status);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getStatus());
    } catch (_) {
      // backend not ready yet
    }
  }, []);

  useEffect(() => {
    void refresh();
    void browserAvailable().then(setHasBrowser).catch(() => setHasBrowser(null));
    const id = setInterval(() => void refresh(), 5_000);
    return () => clearInterval(id);
  }, [refresh]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast(t("failed", { error: String(e) }));
    } finally {
      await refresh();
      setBusy(false);
    }
  };

  const onBackground = (enabled: boolean) =>
    run(async () => {
      const res = await setAutostart(enabled);
      if (res?.error) toast(t("failed", { error: String(res.error) }));
      else toast(enabled ? t("backgroundOn") : t("backgroundOff"));
    });

  const onStartStop = () =>
    run(async () => {
      if (status?.api_reachable) {
        const res = await stopService();
        toast(res?.ok ? t("serviceStopped") : t("failed", { error: String(res?.error ?? "?") }));
      } else {
        const res = await startService();
        toast(res?.ok ? t("serviceStarted") : t("startFailed"));
      }
    });

  const onInstall = () =>
    run(async () => {
      const res = await installApp();
      toast(res?.error ? t("failed", { error: String(res.error) }) : t("installed"));
    });

  const onLibrary = (enabled: boolean) =>
    run(async () => {
      const current = status?.shortcut_app_id ?? 0;
      if (!enabled) {
        if (current) SteamClient.Apps.RemoveShortcut(current);
        await setShortcutAppId(0);
        toast(t("libraryRemoved"));
        return;
      }
      const target = await getLaunchTarget();
      if (!target.exe) {
        toast(t("failed", { error: String(target.error ?? "no AppImage") }));
        return;
      }
      const appId = await SteamClient.Apps.AddShortcut("DeckDrop", target.exe, target.dir ?? "", target.options ?? "");
      // AddShortcut ignores name/options on some client versions – set them explicitly.
      SteamClient.Apps.SetShortcutName(appId, "DeckDrop");
      SteamClient.Apps.SetShortcutLaunchOptions(appId, target.options ?? "--kiosk");
      await setShortcutAppId(appId);
      toast(t("libraryAdded"));
    });

  const onOpen = () => Navigation.NavigateToExternalWeb(status?.url ?? "http://127.0.0.1:7373");

  const onLaunchApp = () => {
    const appId = status?.shortcut_app_id ?? 0;
    const overview = (window as any).appStore?.GetAppOverviewByAppID(appId);
    const gameId = overview?.m_gameid ?? overview?.gameid;
    if (gameId) {
      SteamClient.Apps.RunGame(String(gameId), "", -1, 100);
      Navigation.CloseSideMenus();
    } else {
      Navigation.Navigate(`/library/app/${appId}`);
    }
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
          <span style={{ color: "#f2b544", fontWeight: 500 }}>{t("notInstalled")}</span>
        </PanelSectionRow>
        <PanelSectionRow>
          <span style={{ fontSize: "0.8em", color: "#9e9e9e", lineHeight: 1.5 }}>{t("installHint")}</span>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const running = status.api_reachable;
  const asService = status.service_active;
  const stateText = running
    ? `${asService ? t("running") : t("runningApp")}${status.version ? ` · v${status.version}` : ""}`
    : t("stopped");
  const libraryOn = shortcutExists(status.shortcut_app_id);

  return (
    <>
      <PanelSection title="DeckDrop">
        <PanelSectionRow>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85em" }}>
            <StatusDot color={running ? "#4cc38a" : "#757575"} />
            <span style={{ color: running ? "#4cc38a" : "#9e9e9e" }}>{stateText}</span>
          </div>
        </PanelSectionRow>

        {running && (
          <PanelSectionRow>
            <Field label={t("peers")} bottomSeparator="none">
              {status.peers_online}
            </Field>
          </PanelSectionRow>
        )}

        {status.update_count > 0 && (
          <PanelSectionRow>
            <Field label={t("updatesAvailable")} bottomSeparator="none">
              <span style={{ color: "#4cc38a" }}>{status.update_count}</span>
            </Field>
          </PanelSectionRow>
        )}

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!running || busy} onClick={onOpen} description={t("openDesc")}>
            {t("open")}
          </ButtonItem>
        </PanelSectionRow>

        {libraryOn && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={onLaunchApp}>
              {t("launchApp")}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      {running && (
        <PanelSection title={t("downloads")}>
          {status.downloads.length === 0 ? (
            <PanelSectionRow>
              <span style={{ color: "#9e9e9e", fontSize: "0.85em" }}>{t("noDownloads")}</span>
            </PanelSectionRow>
          ) : (
            status.downloads.slice(0, 4).map((d) => (
              <PanelSectionRow key={d.id}>
                <ProgressBarWithInfo
                  label={d.name}
                  nProgress={Math.round(d.progress * 100)}
                  sOperationText={
                    d.source_state === "waiting"
                      ? t("waitingHost")
                      : d.source_state === "replaced"
                        ? t("newVersion")
                        : `${Math.round(d.progress * 100)} %${d.speed ? ` · ${fmtSpeed(d.speed)}` : ""}`
                  }
                />
              </PanelSectionRow>
            ))
          )}
        </PanelSection>
      )}

      <PanelSection title={t("setup")}>
        {!status.appimage_installed && status.appimage && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={onInstall} description={t("installDesc")}>
              {t("install")}
            </ButtonItem>
          </PanelSectionRow>
        )}

        <PanelSectionRow>
          <ToggleField
            label={t("background")}
            description={t("backgroundDesc")}
            checked={status.service_enabled}
            disabled={busy}
            onChange={(v) => void onBackground(v)}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void onStartStop()}>
            {running ? t("stopService") : t("startService")}
          </ButtonItem>
        </PanelSectionRow>

        {status.appimage && (
          <PanelSectionRow>
            <ToggleField
              label={t("library")}
              description={t("libraryDesc")}
              checked={libraryOn}
              disabled={busy}
              onChange={(v) => void onLibrary(v)}
            />
          </PanelSectionRow>
        )}

        {status.appimage && hasBrowser === false && (
          <PanelSectionRow>
            <span style={{ fontSize: "0.8em", color: "#f2b544", lineHeight: 1.5 }}>{t("noBrowser")}</span>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
};

export default definePlugin(() => ({
  name: "DeckDrop",
  title: <div className={staticClasses.Title}>DeckDrop</div>,
  content: <Content />,
  icon: <FaShare />,
  onDismount() {},
}));
