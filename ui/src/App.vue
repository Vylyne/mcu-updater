<script setup lang="ts">
// Phase 3 debug harness: connection state, the fw.ping result, live
// fw.status JSON and a rolling event log - the verification surface
// docs/plan called for before there is a real targets[] view (Phase 4).
import { computed, onMounted, onUnmounted, ref } from "vue";
import { httpGetJson } from "./api/moonraker";
import { connect, disconnect, state } from "./store/agent";
import TargetsView from "./components/TargetsView.vue";
import JobPanel from "./components/JobPanel.vue";
import KconfigDialog from "./components/KconfigDialog.vue";
import BusPanel from "./components/BusPanel.vue";
import AddMcuWizard from "./components/AddMcuWizard.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import type { Target } from "./api/targets";

const API_KEY_STORAGE_KEY = "mcu-updater-ui:apiKey";

function readStoredApiKey(): string | null {
  try {
    return localStorage.getItem(API_KEY_STORAGE_KEY);
  } catch {
    // Private browsing, disabled storage, or (in tests) no localStorage at all.
    return null;
  }
}

function defaultSocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const apiKey = readStoredApiKey();
  const token = apiKey ? `?token=${encodeURIComponent(apiKey)}` : "";
  return `${protocol}//${window.location.host}/websocket${token}`;
}

const socketUrl = ref(defaultSocketUrl());
const accessInfo = ref<Record<string, unknown> | null>(null);
const accessError = ref<string | null>(null);

// ?embed=1 is the Mainsail "HTML Iframe" webcam service - see
// docs/standalone-ui.md. It drops everything that is only useful standing
// alone at a full page (title, connection debug, the raw JSON dumps, the
// event log) and keeps only the functional surface, inside a box the iframe
// itself sizes - never the viewport, which inside an iframe is Mainsail's.
const isEmbed =
  new URLSearchParams(window.location.search).get("embed") === "1";

const statusText = computed(() => JSON.stringify(state.status, null, 2));
const pingText = computed(() => JSON.stringify(state.ping, null, 2));
const targets = computed(() => state.status?.targets as Target[] | undefined);

async function loadAccessInfo(): Promise<void> {
  try {
    const apiKey = readStoredApiKey() ?? undefined;
    accessInfo.value = await httpGetJson<Record<string, unknown>>(
      "/access/info",
      apiKey,
    );
    accessError.value = null;
  } catch (error) {
    accessError.value =
      (error as { message?: string }).message ??
      "Could not reach /access/info.";
  }
}

onMounted(() => {
  if (isEmbed) {
    // Sizes main.embed to the iframe's own box rather than the viewport -
    // #app and body still need height:100% for that percentage to resolve
    // to anything, and Vue 3 leaves #app itself in the DOM after mounting.
    document.documentElement.classList.add("mcu-updater-embed");
    document.body.classList.add("mcu-updater-embed");
  }
  void loadAccessInfo();
  connect(socketUrl.value);
});

onUnmounted(() => {
  disconnect();
});

function reconnect(): void {
  connect(socketUrl.value);
}
</script>

<template>
  <main :class="{ embed: isEmbed }">
    <template v-if="!isEmbed">
      <h1>mcu-updater</h1>

      <section>
        <h2>Connection</h2>
        <p>state: {{ state.connection }}</p>
        <p>agent available: {{ state.agentAvailable }}</p>
        <button type="button" @click="reconnect">Reconnect</button>
        <p v-if="accessError">access/info: {{ accessError }}</p>
        <p v-else-if="accessInfo">
          access/info: {{ accessInfo.default_source ?? "trusted" }}
          <span v-if="(accessInfo.default_source ?? null) !== null">
            (force_logins installs need the login flow, not yet supported by
            this UI)
          </span>
        </p>
      </section>
    </template>

    <section v-if="state.unsupportedApiVersion !== null">
      <h2>Update required</h2>
      <p>
        The agent speaks api_version {{ state.unsupportedApiVersion }}, which
        this UI does not understand yet. Update mcu-updater-ui.
      </p>
    </section>

    <section v-if="state.error">
      <h2>Error</h2>
      <p>{{ state.error.code }}: {{ state.error.message }}</p>
    </section>

    <section v-if="!isEmbed">
      <h2>fw.ping</h2>
      <pre>{{ pingText }}</pre>
    </section>

    <TargetsView :targets="targets" />
    <BusPanel />
    <AddMcuWizard />

    <JobPanel />
    <KconfigDialog />

    <SettingsPanel v-if="!isEmbed" />

    <template v-if="!isEmbed">
      <section>
        <h2>fw.status (raw)</h2>
        <pre>{{ statusText }}</pre>
      </section>

      <section>
        <h2>Events</h2>
        <ul>
          <li v-for="(entry, index) in state.events" :key="index">
            {{ new Date(entry.at).toLocaleTimeString() }} - {{ entry.event }}
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>
