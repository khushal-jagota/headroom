<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import ResourceState from "../components/ResourceState.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import { errorMessage } from "../lib/ui";

  type DeviceState =
    | "checking"
    | "unsupported"
    | "install"
    | "ready"
    | "blocked"
    | "enabled"
    | "working";

  const settings = createQuery(() => queries.notificationSettings());
  let saving = $state<Record<string, boolean>>({});
  let saveErrors = $state<Record<string, unknown>>({});
  let deviceState = $state<DeviceState>("checking");
  let deviceError = $state<unknown>(null);
  let subscriptionId = $state<string | null>(null);

  function base64UrlBytes(value: string): Uint8Array<ArrayBuffer> {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const decoded = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) {
      bytes[index] = decoded.charCodeAt(index);
    }
    return bytes;
  }

  function isIosBrowserOutsideStandalone(): boolean {
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
    return ios && !standalone;
  }

  async function registerWithPanels(subscription: PushSubscription): Promise<string> {
    const json = subscription.toJSON();
    const response = await mutateJson<{ subscription_id: string }>(
      "/api/notifications/subscriptions",
      {
        method: "POST",
        body: {
          endpoint: subscription.endpoint,
          keys: {
            p256dh: json.keys?.p256dh,
            auth: json.keys?.auth
          }
        }
      }
    );
    return response.subscription_id;
  }

  async function reconcileDevice(): Promise<void> {
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

  function preferenceKey(subjectKey: string, notificationType: string): string {
    return `${subjectKey}:${notificationType}`;
  }

  async function savePreference(
    subjectKey: string,
    notificationType: string,
    enabled: boolean
  ): Promise<void> {
    const key = preferenceKey(subjectKey, notificationType);
    saving = { ...saving, [key]: true };
    saveErrors = { ...saveErrors, [key]: null };
    try {
      await mutateJson(
        `/api/notifications/preferences/${encodeURIComponent(subjectKey)}/${encodeURIComponent(notificationType)}`,
        {
          method: "PUT",
          body: { enabled }
        }
      );
    } catch (error) {
      saveErrors = { ...saveErrors, [key]: error };
    } finally {
      saving = { ...saving, [key]: false };
    }
  }

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
    deviceState = "working";
    deviceError = null;
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription && !subscriptionId) subscriptionId = await registerWithPanels(subscription);
      if (subscriptionId) {
        await mutateJson(`/api/notifications/subscriptions/${subscriptionId}`, {
          method: "DELETE"
        });
      }
      await subscription?.unsubscribe();
      subscriptionId = null;
      deviceState = "ready";
    } catch (error) {
      deviceError = error;
      deviceState = "enabled";
    }
  }

</script>

<section class="notifications-page" data-screen="notifications">
  <header class="notifications-head">
    <div class="eyebrow">Settings</div>
    <h1>Notifications</h1>
  </header>

  <ResourceState {...resourceStateForQueries(settings)} loadingText="Loading notifications…">
    <section class="notifications-section">
      <h2>What counts</h2>
      {#each settings.data?.subjects || [] as subject (subject.key)}
        <div class="notification-subject" data-notification-subject={subject.key}>
          <h3>{subject.label}</h3>
          <div class="notification-options">
            {#each subject.types as item (item.id)}
              {@const key = preferenceKey(subject.key, item.id)}
              <label
                class="notification-option"
                data-notification-cell={key}
                data-notification-type={item.id}
              >
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
                <input
                  type="checkbox"
                  checked={item.enabled}
                  disabled={saving[key]}
                  onchange={(event) =>
                    void savePreference(subject.key, item.id, event.currentTarget.checked)}
                />
              </label>
              {#if saveErrors[key]}
                <div class="error-line">{errorMessage(saveErrors[key])}</div>
              {/if}
            {/each}
          </div>
        </div>
        {/each}
    </section>

    <section class="notifications-section" data-device-state={deviceState}>
      <h2>This device</h2>
      {#if deviceState === "checking"}
        <p>Checking notification support…</p>
      {:else if deviceState === "install"}
        <p>Add Panels to the Home Screen, open that app, then enable notifications here.</p>
      {:else if deviceState === "unsupported"}
        <p>This browser does not support Web Push. Panels continues to work normally.</p>
      {:else if deviceState === "blocked"}
        <p>Notifications are blocked in this browser’s settings.</p>
      {:else if deviceState === "enabled"}
        <p>This device receives the notification types selected above.</p>
        <button type="button" class="notification-action" onclick={() => void disableDevice()}>
          Disable on this device
        </button>
      {:else}
        <p>Enable this device to receive selected notifications while Panels is closed.</p>
        <button
          type="button"
          class="notification-action notification-action--primary"
          disabled={deviceState === "working"}
          onclick={() => void enableDevice()}
        >
          {deviceState === "working" ? "Enabling…" : "Enable on this device"}
        </button>
      {/if}
      {#if deviceError}
        <div class="error-line">{errorMessage(deviceError)}</div>
      {/if}
    </section>
  </ResourceState>
</section>
