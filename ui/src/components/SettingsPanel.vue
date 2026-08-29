<script setup lang="ts">
// fw.settings.get/set - the SETTABLE subset of settings.py's Settings, edited
// as one small form. state.status.settings already carries the full current
// set (fw.status embeds it), so there is no separate load call.
import { computed, reactive, ref, watch } from "vue";
import { state, updateSettings } from "../store/agent";
import {
  RECONNECT_REQUIRED_KEYS,
  SETTABLE_KEYS,
  type SettableKey,
  type UpdaterSettings,
} from "../api/settings";
import UiPanel from "./UiPanel.vue";

const settings = computed(
  () => state.status?.settings as UpdaterSettings | undefined,
);

// A local editable copy, refreshed from the server value whenever it moves
// under us (another tab saved, or a reconnect re-fetched fw.status) and we
// have no pending edit of our own to lose.
const draft = reactive<Partial<Record<SettableKey, number | boolean | string>>>(
  {},
);
const saving = ref(false);

// `<input type="color">` can never itself produce an empty string, so an
// unset (cleared-to-default) accent needs something to paint the swatch
// with - matches this app's own --color-primary default in style.css, not
// picked independently of it.
const DEFAULT_ACCENT = "#2196f3";

const accentValue = computed(
  () => (draft.ui_accent_color as string) || DEFAULT_ACCENT,
);

function onAccentInput(event: Event): void {
  draft.ui_accent_color = (event.target as HTMLInputElement).value;
}

watch(
  settings,
  (value) => {
    if (!value) return;
    for (const key of SETTABLE_KEYS) draft[key] = value[key];
  },
  { immediate: true },
);

const dirty = computed(() => {
  if (!settings.value) return false;
  return SETTABLE_KEYS.some((key) => draft[key] !== settings.value![key]);
});

const reconnectNote = computed(() => {
  if (!settings.value) return false;
  return SETTABLE_KEYS.some(
    (key) =>
      RECONNECT_REQUIRED_KEYS.has(key) && draft[key] !== settings.value![key],
  );
});

async function save(): Promise<void> {
  if (!settings.value) return;
  saving.value = true;
  const patch: Record<string, unknown> = {};
  for (const key of SETTABLE_KEYS) {
    if (draft[key] !== settings.value[key]) patch[key] = draft[key];
  }
  await updateSettings(patch);
  saving.value = false;
}

function discard(): void {
  if (!settings.value) return;
  for (const key of SETTABLE_KEYS) draft[key] = settings.value[key];
}
</script>

<template>
  <UiPanel
    v-if="settings"
    title="Settings"
    collapsible
    storage-key="settings"
    :default-expanded="false"
  >
    <div class="settings-grid">
      <label>
        make_jobs (-1 = one per CPU, 0 = no -j flag)
        <input
          v-model.number="draft.make_jobs"
          type="number"
          min="-1"
          max="64"
        />
      </label>

      <label>
        log_ring_size (lines kept per job)
        <input
          v-model.number="draft.log_ring_size"
          type="number"
          min="100"
          max="100000"
        />
      </label>

      <label>
        clean_before_build
        <input
          v-model="draft.clean_before_build"
          type="checkbox"
          class="switch"
        />
      </label>

      <label>
        reseed_on_build
        <input v-model="draft.reseed_on_build" type="checkbox" class="switch" />
      </label>

      <label>
        dry_run
        <input v-model="draft.dry_run" type="checkbox" class="switch" />
      </label>

      <label>
        enable_flashing
        <input v-model="draft.enable_flashing" type="checkbox" class="switch" />
      </label>

      <label>
        allow_flash_while_printing
        <input
          v-model="draft.allow_flash_while_printing"
          type="checkbox"
          class="switch"
        />
      </label>
    </div>

    <!-- Not part of settings-grid's number/switch rows above - this one is
         cosmetic, not a behaviour preference, and its "clear" affordance
         doesn't fit that grid's single-control-per-row shape. Stored on the
         agent anyway (not localStorage) so every browser pointed at this
         printer agrees, per settings.py's own ui_accent_color comment. -->
    <label class="accent-row">
      Accent colour
      <span class="accent-controls">
        <input :value="accentValue" type="color" @input="onAccentInput" />
        <button
          type="button"
          :disabled="!draft.ui_accent_color"
          @click="draft.ui_accent_color = ''"
        >
          Reset to default
        </button>
      </span>
    </label>

    <p class="alert alert--info">
      stop_services and service_backend describe how this host is wired, not a
      behaviour preference, and are edited in the cfg file directly rather than
      from here.
    </p>

    <p v-if="reconnectNote" class="alert alert--info">
      enable_flashing / allow_flash_while_printing take effect once the agent
      reconnects to Moonraker - Moonraker only registers the flashing methods at
      handshake, so a flash button here can stay unusable (or stay usable) until
      then even after this saves.
    </p>

    <button type="button" :disabled="!dirty || saving" @click="save">
      {{ saving ? "Saving…" : "Save" }}
    </button>
    <button type="button" :disabled="!dirty" @click="discard">Discard</button>
  </UiPanel>
</template>

<style scoped>
.accent-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
}

.accent-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.accent-controls input[type="color"] {
  width: 40px;
  height: 28px;
  padding: 2px;
  cursor: pointer;
}
</style>
