<script lang="ts">
  import Button from "../components/Button.svelte";
  import SkeletonRegion from "../components/SkeletonRegion.svelte";
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
  <ResourceState shape="block" {...resourceStateForQueries(settings)} loadingText="Loading notifications…">
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
                <span class="notification-copy">
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
                <span class="notification-choice">
                  {#if saving[key]}<span class="notification-saving" role="status">Saving…</span>{/if}
                  <input
                    type="checkbox"
                    checked={item.enabled}
                    disabled={saving[key]}
                    onchange={(event) =>
                      void savePreference(subject.key, item.id, event.currentTarget.checked)}
                  />
                </span>
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
      <SkeletonRegion shape="block" loading={deviceState === "checking"} loadingText="Checking notification support…">
      {#if deviceState === "install"}
        <p>Add Panels to the Home Screen, open that app, then enable notifications here.</p>
      {:else if deviceState === "unsupported"}
        <p>This browser does not support Web Push. Panels continues to work normally.</p>
      {:else if deviceState === "blocked"}
        <p>Notifications are blocked in this browser’s settings.</p>
      {:else if deviceState === "enabled"}
        <p>This device receives the notification types selected above.</p>
        <Button variant="secondary" onclick={() => void disableDevice()}>
          Disable on this device
        </Button>
      {:else if deviceState !== "checking"}
        <p>Enable this device to receive selected notifications while Panels is closed.</p>
        <Button
          variant="primary"
          disabled={deviceState === "working"}
          onclick={() => void enableDevice()}
        >
          {deviceState === "working" ? "Enabling…" : "Enable on this device"}
        </Button>
      {/if}
      </SkeletonRegion>
      {#if deviceError}
        <div class="error-line">{errorMessage(deviceError)}</div>
      {/if}
    </section>
  </ResourceState>
</section>

<style>
  .notifications-page { width:100%; padding:var(--page-block-gap) 0 var(--space-page-tail); }
  .notifications-section + .notifications-section { margin-top:var(--page-section-gap); }
  .notifications-section > h2 { margin:0 0 var(--page-head-gap); color:var(--tone-tertiary); font-size:var(--type-small); font-weight:var(--weight-small); }
  .notification-subject + .notification-subject { margin-top:var(--page-block-gap); }
  .notification-subject h3 { margin:0 0 var(--space-2); font-size:var(--type-page-section); font-weight:var(--weight-page-section); color:var(--tone-primary); }
  .notification-option { display:flex; justify-content:space-between; align-items:center; gap:var(--space-4); padding:var(--space-3) 0; cursor:pointer; }
  .notification-copy { min-width:0; }
  .notification-copy strong,.notification-copy small { display:block; }
  .notification-copy strong { color:var(--tone-primary); font-size:var(--type-body); font-weight:var(--weight-body); }
  .notification-copy small,.notifications-section p { margin:var(--space-1) 0 0; color:var(--tone-secondary); font-size:var(--type-small); line-height:1.55; }
  .notification-choice { display:flex; flex:none; align-items:center; gap:var(--space-2); }
  .notification-saving { font-size:var(--type-small); color:var(--tone-tertiary); }
  input[type="checkbox"] { appearance:none; display:grid; place-content:center; width:var(--space-5); height:var(--space-5); margin:0; border:var(--border-hairline) solid var(--tone-tertiary); border-radius:var(--radius-sm); background:transparent; color:var(--button-primary-ink); cursor:pointer; }
  input[type="checkbox"]::before { content:""; width:var(--space-2); height:var(--space-3); border:solid currentColor; border-width:0 var(--border-strong) var(--border-strong) 0; transform:translateY(calc(-1 * var(--border-hairline))) rotate(45deg); opacity:0; }
  input[type="checkbox"]:checked { background:var(--button-primary-fill); border-color:transparent; }
  input[type="checkbox"]:checked::before { opacity:1; }
  input[type="checkbox"]:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--space-1); }
  input[type="checkbox"]:disabled { opacity:.5; cursor:wait; }
  .notifications-section :global(.button) { margin-top:var(--space-3); }
  .error-line { margin-top:var(--space-2); color:var(--accent-error); font-size:var(--type-small); }
  @media (forced-colors:active) { input[type="checkbox"] { appearance:auto; } input[type="checkbox"]::before { display:none; } }
</style>
