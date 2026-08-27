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

const settings = computed(
  () => state.status?.settings as UpdaterSettings | undefined,
);

// A local editable copy, refreshed from the server value whenever it moves
// under us (another tab saved, or a reconnect re-fetched fw.status) and we
// have no pending edit of our own to lose.
const draft = reactive<Partial<Record<SettableKey, number | boolean>>>({});
const saving = ref(false);

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
  <section v-if="settings" class="settings">
    <h2>Settings</h2>

    <label>
      <input v-model.number="draft.make_jobs" type="number" min="-1" max="64" />
      make_jobs (-1 = one per CPU, 0 = no -j flag)
    </label>

    <label>
      <input
        v-model.number="draft.log_ring_size"
        type="number"
        min="100"
        max="100000"
      />
      log_ring_size (lines kept per job)
    </label>

    <label>
      <input v-model="draft.clean_before_build" type="checkbox" />
      clean_before_build
    </label>

    <label>
      <input v-model="draft.reseed_on_build" type="checkbox" />
      reseed_on_build
    </label>

    <label>
      <input v-model="draft.dry_run" type="checkbox" />
      dry_run
    </label>

    <label>
      <input v-model="draft.enable_flashing" type="checkbox" />
      enable_flashing
    </label>

    <label>
      <input v-model="draft.allow_flash_while_printing" type="checkbox" />
      allow_flash_while_printing
    </label>

    <p class="muted">
      stop_services and service_backend describe how this host is wired, not a
      behaviour preference, and are edited in the cfg file directly rather than
      from here.
    </p>

    <p v-if="reconnectNote" class="muted">
      enable_flashing / allow_flash_while_printing take effect once the agent
      reconnects to Moonraker - Moonraker only registers the flashing methods at
      handshake, so a flash button here can stay unusable (or stay usable) until
      then even after this saves.
    </p>

    <button type="button" :disabled="!dirty || saving" @click="save">
      {{ saving ? "Saving…" : "Save" }}
    </button>
    <button type="button" :disabled="!dirty" @click="discard">Discard</button>
  </section>
</template>
