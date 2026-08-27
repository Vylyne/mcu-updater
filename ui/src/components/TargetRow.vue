<script setup lang="ts">
// One targets[] row, rendered the same way for an MCU or a display - see
// docs/decisions.md and docs/agent-api.md's "targets" section for why the
// two are one shape. Phase 4 renders the row only: no actions yet, those are
// Phase 5's {id, label, method, params, blocked, choices?} renderer.
import { computed, ref } from "vue";
import { fetchTargetDetail } from "../store/agent";
import type { Target } from "../api/targets";

const props = defineProps<{ target: Target }>();

const expanded = ref(false);
const loading = ref(false);
const detail = ref<Record<string, unknown> | null>(null);

const detailText = computed(() =>
  detail.value ? JSON.stringify(detail.value, null, 2) : "",
);

// A display always carries `extra.klipper_section`; an MCU row carries no
// `extra` at all. Reading that presence, not `target.provider`, is what
// keeps this row generic - the provider branch the fork has here is exactly
// the one this file exists to not repeat.
//
// `extra.reachable` gets its own branch first: docs/agent-api.md's
// fw.device.list section is explicit that "no displays configured" and "we
// could not ask Klipper" must not look the same, because the module that
// would otherwise report a screen missing is exactly the thing an
// unreachable Klipper takes down too.
const noDevicesHint = computed(() => {
  const extra = props.target.extra;
  if (!extra) return "No serial devices are tracked for this type yet.";
  if (!extra.reachable) return "Could not reach Klipper to check for screens.";
  return `No screens found under [${extra.klipper_section} ...].`;
});

async function toggle(): Promise<void> {
  if (expanded.value) {
    expanded.value = false;
    return;
  }
  expanded.value = true;
  if (detail.value !== null) return;
  loading.value = true;
  detail.value = await fetchTargetDetail(
    props.target.name,
    props.target.provider,
  );
  loading.value = false;
}
</script>

<template>
  <article class="target" :data-tone="target.artifact.tone">
    <header>
      <h3>{{ target.name }}</h3>
      <span class="chip">{{ target.provider }}</span>
      <span class="chip" :data-tone="target.artifact.tone">{{
        target.artifact.label
      }}</span>
    </header>
    <p class="descriptor">
      {{ target.descriptor }}
    </p>

    <ul v-if="target.devices.length" class="devices">
      <li
        v-for="device in target.devices"
        :key="device.id"
        :data-tone="device.tone"
      >
        <span>{{ device.name ?? device.id }}</span>
        <span class="chip" :data-tone="device.tone">{{ device.label }}</span>
        <span v-if="!device.present" class="muted">not present</span>
      </li>
    </ul>
    <p v-else class="muted">
      {{ noDevicesHint }}
    </p>

    <button type="button" @click="toggle">
      {{ expanded ? "Hide detail" : "Show detail" }}
    </button>
    <p v-if="expanded && loading">Loading…</p>
    <pre v-else-if="expanded && detail">{{ detailText }}</pre>
  </article>
</template>
