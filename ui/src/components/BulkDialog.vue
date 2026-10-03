<script setup lang="ts">
// One confirmation for build_all/flash_all/update_all - mirroring
// The bulk dialog, minus the `name` filter: agent-api.md's
// methods table gives fw.build_all only `fw?, scope?`, no `name`, and rows
// already carry per-type flash through their own actions[], so nothing is
// lost by staying fleet-wide only.
import { computed, ref, watch } from "vue";
import {
  bulkBuildTargets,
  bulkFlashTargets,
  bulkHasWork,
  type BulkOperation,
  type BulkScope,
} from "../api/bulk";
import { printerBusy, runBulk, state } from "../store/agent";
import type { Target } from "../api/targets";
import UiDialog from "./UiDialog.vue";
import BulkPreview, { type PreviewRow } from "./BulkPreview.vue";

const props = defineProps<{
  operation: BulkOperation;
  targets: Target[];
}>();

const emit = defineEmits<{ close: [] }>();

// "Everything, not just what looks stale" never latches - reopening this
// dialog with the last run's scope still selected would make the
// deliberate, occasional choice the default for whoever opens it next.
const scope = ref<BulkScope>("stale");
watch(
  () => props.operation,
  () => {
    scope.value = "stale";
  },
  { immediate: true },
);

const writesToBoards = computed(() => props.operation !== "build_all");
const showsBuilds = computed(() => props.operation !== "flash_all");
const showsFlashes = computed(() => props.operation !== "build_all");

const title = computed(() => {
  if (props.operation === "build_all") return "Build everything";
  if (props.operation === "flash_all") return "Flash everything";
  return "Update everything";
});

const body = computed(() => {
  if (props.operation === "build_all") {
    return "Build every target whose artifact needs it. Nothing is written to a board.";
  }
  if (props.operation === "flash_all") {
    return "Flash every device that needs it. This stops Klipper once for the whole batch.";
  }
  return "Build what needs it, then flash what needs it. This stops Klipper once, after the builds finish.";
});

const buildRows = computed<PreviewRow[] | null>(() =>
  showsBuilds.value
    ? bulkBuildTargets(props.targets, scope.value).map((target) => ({
        key: target.name,
        name: target.name,
        detail: target.artifact.label,
      }))
    : null,
);
const flashRows = computed<PreviewRow[] | null>(() =>
  showsFlashes.value
    ? bulkFlashTargets(props.targets, scope.value).map((entry) => ({
        key: entry.id,
        name: entry.name ?? entry.type,
        detail: `${entry.type} · ${entry.id}`,
      }))
    : null,
);
const hasWork = computed(() =>
  bulkHasWork(props.targets, props.operation, scope.value),
);

const busy = computed(() => writesToBoards.value && printerBusy());
const busyMessage = computed(() =>
  state.status?.printing === true
    ? "The printer is printing - flashing would shut the MCU down mid-print."
    : "The printer is moving - flashing would shut the MCU down mid-motion.",
);

const running = ref(false);

async function confirm(): Promise<void> {
  running.value = true;
  await runBulk(props.operation, scope.value);
  running.value = false;
  emit("close");
}
</script>

<template>
  <UiDialog :title="title" @close="emit('close')">
    <BulkPreview
      v-model:scope="scope"
      :body="body"
      offers-scope
      :builds="buildRows"
      :flashes="flashRows"
      :has-work="hasWork"
      :floor-note="operation === 'update_all'"
      :writes-to-boards="writesToBoards"
      :busy-message="busy ? busyMessage : null"
    />

    <template #actions>
      <button type="button" @click="emit('close')">Cancel</button>
      <button
        type="button"
        :disabled="!hasWork || busy || running"
        @click="confirm"
      >
        {{ running ? "Working…" : title }}
      </button>
    </template>
  </UiDialog>
</template>
