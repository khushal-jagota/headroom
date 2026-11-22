<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import SettingsSwitchRow from "./SettingsSwitchRow.svelte";
  import { mutateJson } from "../../lib/mutate";
  import { queries } from "../../lib/queryCatalogue";
  import { errorMessage } from "../../lib/ui";

  /* Notifications: this device's Web Push subscription, then one switch per notification
     type of each subject. The server's preferences are the saved values; a press saves at
     once and the page's read refreshes after it. */
  type DeviceState = "checking" | "desktop" | "unsupported" | "install" | "ready" | "blocked" | "enabled" | "working";

  const settings = createQuery(() => queries.notificationSettings());
  let deviceState = $state<DeviceState>("checking");
  let deviceError = $state<unknown>(null);
  let turningOff = $state(false);
  let subscriptionId = $state<string | null>(null);

  const deviceDescription: Record<Exclude<DeviceState, "checking">, string> = {
    desktop: "This Mac gets the notifications chosen below while the Panels app runs, also with its window closed. macOS Settings control banners and sounds.",
    enabled: "Gets the notifications chosen below while Panels is closed.",
    working: "Asking this browser for permission…",
    blocked: "Notifications are blocked in this browser’s settings.",
    unsupported: "This browser does not support Web Push. Panels continues to work normally.",
    install: "Add Panels to the Home Screen, open that app, then turn notifications on here.",
    ready: "Off. Turn on to get notifications while Panels is closed."
  };

  function base64UrlBytes(value: string): Uint8Array<ArrayBuffer> {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const decoded = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
    return bytes;
  }

  function isIosBrowserOutsideStandalone(): boolean {
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
    return ios && !standalone;
  }

  async function registerWithPanels(subscription: PushSubscription): Promise<string> {
    const json = subscription.toJSON();
    const response = await mutateJson<{ subscription_id: string }>("/api/notifications/subscriptions", {
      method: "POST",
      body: { endpoint: subscription.endpoint, keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth } }
    });
    return response.subscription_id;
  }

  async function reconcileDevice(): Promise<void> {
    // The desktop app serves the screens from its own scheme. Its native side shows the notifications.
    if (location.protocol === "panels:") {
      deviceState = "desktop";
      return;
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      deviceState = isIosBrowserOutsideStandalone() ? "install" : "unsupported";
      return;
    }
    if (Notification.permission === "denied") {
      deviceState = "blocked";
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      deviceState = "ready";
      return;
    }
    subscriptionId = await registerWithPanels(subscription);
    deviceState = "enabled";
  }

  onMount(() => {
    void reconcileDevice().catch((error) => {
      deviceError = error;
      deviceState = "ready";
    });
  });

  async function enableDevice(): Promise<void> {
    deviceState = "working";
    deviceError = null;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        deviceState = permission === "denied" ? "blocked" : "ready";
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      const subscription =
        existing ??
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64UrlBytes(settings.data?.vapid_public_key ?? "")
        }));
      subscriptionId = await registerWithPanels(subscription);
      deviceState = "enabled";
    } catch (error) {
      deviceError = error;
      deviceState = "ready";
    }
  }

  async function disableDevice(): Promise<void> {
    turningOff = true;
    deviceError = null;
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription && !subscriptionId) subscriptionId = await registerWithPanels(subscription);
      if (subscriptionId) {
        await mutateJson(`/api/notifications/subscriptions/${subscriptionId}`, { method: "DELETE" });
      }
      await subscription?.unsubscribe();
      subscriptionId = null;
      deviceState = "ready";
    } catch (error) {
      deviceError = error;
      deviceState = "enabled";
    } finally {
      turningOff = false;
    }
  }

  function savePreference(subjectKey: string, notificationType: string, enabled: boolean): Promise<unknown> {
    return mutateJson(
      `/api/notifications/preferences/${encodeURIComponent(subjectKey)}/${encodeURIComponent(notificationType)}`,
      { method: "PUT", body: { enabled } }
    );
  }
</script>

<SettingsPage title="Notifications">
  {#if settings.data}
    <SettingsSection title="This device" first>
      {#if deviceState === "checking"}
        <SettingsRow label="Push notifications" rowId="notifications:device">
          {#snippet description()}<span class="notification-checking-line" aria-hidden="true"></span><span class="skeleton-status" role="status">Checking notification support…</span>{/snippet}
        </SettingsRow>
      {:else}
        <SettingsRow label="Push notifications" description={deviceDescription[deviceState]} rowId="notifications:device"
          error={deviceError ? errorMessage(deviceError) : null}>
          {#snippet control()}
            {#if deviceState === "enabled"}
              <Button variant="secondary" disabled={turningOff} onclick={() => void disableDevice()}>Turn off</Button>
            {:else if deviceState === "working"}
              <Button variant="primary" disabled>Turning on…</Button>
            {:else if deviceState === "ready"}
              <Button variant="primary" onclick={() => void enableDevice()}>Turn on</Button>
            {/if}
          {/snippet}
        </SettingsRow>
      {/if}
    </SettingsSection>
    {#each settings.data.subjects as subject (subject.key)}
      <SettingsSection title={subject.label}>
        {#each subject.types as item (item.id)}
          <SettingsSwitchRow label={item.label} description={item.description} rowId={`notifications:${subject.key}:${item.id}`}
            checked={item.enabled} save={(next) => savePreference(subject.key, item.id, next)}
            switchLabel={`${subject.label}: ${item.label}`} failureText="Could not save. Panels did not change this notification." />
        {/each}
      </SettingsSection>
    {/each}
  {:else if settings.error}
    <ErrorLine error={settings.error} />
  {:else}
    <span class="skeleton-status" role="status">Loading notifications…</span>
    <div class="notification-skeleton" aria-hidden="true">
      <SettingsSection first>
        {#each Array.from({ length: 6 }, (_, index) => index) as index}
          <div class="notification-skeleton-row">
            <div class="notification-skeleton-label">
              <span class="notification-skeleton-line" style:width={`${30 + ((index * 17) % 35)}%`}></span>
              <span class="notification-skeleton-line notification-skeleton-line--description" style:width={`${45 + ((index * 23) % 30)}%`}></span>
            </div>
            <span class="notification-skeleton-switch"></span>
          </div>
        {/each}
      </SettingsSection>
    </div>
  {/if}
</SettingsPage>

<style>
  /* The skeleton waits the shared skeleton delay before it fades in, so a quick read never flashes it. */
  .notification-skeleton { animation:notification-skeleton-in var(--skeleton-fade) ease-in-out var(--skeleton-delay) both; }
  .notification-skeleton-row { display:flex; align-items:center; gap:var(--space-4); min-height:52px; }
  .notification-skeleton-label { flex:1; min-width:0; }
  .notification-skeleton-line, .notification-checking-line { display:block; height:10px; border-radius:var(--radius-sm); background:var(--skeleton-line); }
  .notification-skeleton-line--description { height:8px; margin-top:var(--space-2); }
  .notification-skeleton-switch { flex:none; width:34px; height:20px; border-radius:var(--radius-pill); background:var(--skeleton-line); }
  /* While this browser is checked, the description is one quiet line the height of its ink. */
  .notification-checking-line { width:60%; height:8px; margin:calc((1lh - 8px) / 2) 0; }
  @keyframes notification-skeleton-in { from { opacity:0; } to { opacity:1; } }
  @media (prefers-reduced-motion: reduce) { .notification-skeleton { animation:none; } }
</style>
