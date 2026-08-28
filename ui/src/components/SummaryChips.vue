<script setup lang="ts">
// The fleet-wide counts across the top of the panel - FirmwareUpdaterPanel.vue's
// v-chip row, said over `targets[]` rather than `types`. That distinction is
// load-bearing: the fork's own comments record that counting `types` alone
// let "all up to date" go green with a screen three commits behind, because
// displays were left out and a type's verdict was read from an artifact it
// would never build. Six independent counts, each answering a different
// question - stale/unprovable is about artifacts needing a rebuild,
// needsFlash/allFlashed is about devices needing a flash, and they must not
// be conflated.
import { computed } from "vue";
import type { Target } from "../api/targets";

const props = defineProps<{ targets: Target[] }>();

const staleCount = computed(
  () => props.targets.filter((t) => t.artifact.state === "stale").length,
);

// A separate claim from stale, and deliberately not amber: nobody can vouch
// for this image either way, which is a different thing to be told than
// "rebuild it".
const unprovableCount = computed(
  () => props.targets.filter((t) => t.artifact.state === "unprovable").length,
);

const needsFlashCount = computed(() =>
  props.targets.reduce(
    (total, t) =>
      total + t.devices.filter((d) => d.needs_flash === true).length,
    0,
  ),
);

// Devices we cannot vouch for either way - offline, or reachable but saying
// nothing comparable. Kept separate from needsFlashCount so an unanswerable
// fleet never reads as a clean one.
const unknownCount = computed(() =>
  props.targets.reduce(
    (total, t) =>
      total + t.devices.filter((d) => d.needs_flash === null).length,
    0,
  ),
);

const offlineCount = computed(() =>
  props.targets.reduce(
    (total, t) => total + t.devices.filter((d) => !d.present).length,
    0,
  ),
);

const boardCount = computed(() =>
  props.targets.reduce((total, t) => total + t.devices.length, 0),
);

// Only claim everything is flashed when everything actually answered - an
// unreachable board is not evidence that it is current.
const allFlashed = computed(
  () =>
    needsFlashCount.value === 0 &&
    unknownCount.value === 0 &&
    boardCount.value > 0,
);
</script>

<template>
  <div class="summary-chips">
    <span
      class="chip chip--x-small"
      :data-tone="staleCount ? 'attention' : 'ok'"
    >
      {{
        staleCount
          ? `${staleCount}/${targets.length} need a rebuild`
          : "All up to date"
      }}
    </span>
    <span v-if="unprovableCount" class="chip chip--x-small chip--outlined">
      {{ unprovableCount }} unprovable
    </span>
    <span v-if="allFlashed" class="chip chip--x-small" data-tone="ok">
      All flashed
    </span>
    <span v-if="needsFlashCount" class="chip chip--x-small chip--outlined">
      {{ needsFlashCount }} need flashing
    </span>
    <span class="chip chip--x-small chip--outlined">
      {{ boardCount }} board{{ boardCount === 1 ? "" : "s" }}
    </span>
    <span v-if="offlineCount" class="chip chip--x-small" data-tone="attention">
      {{ offlineCount }} offline
    </span>
  </div>
</template>

<style scoped>
.summary-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
</style>
