<script setup lang="ts">
// One confirmation for build_all/flash_all/update_all - mirroring
// FirmwareUpdaterPanelBulkDialog.vue, minus the `name` filter: agent-api.md's
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
    return "Flash every board and screen that needs it. This stops Klipper once for the whole batch.";
  }
  return "Build what needs it, then flash what needs it. This stops Klipper once, after the builds finish.";
});

const buildTargets = computed(() =>
  bulkBuildTargets(props.targets, scope.value),
);
const flashTargets = computed(() =>
  bulkFlashTargets(props.targets, scope.value),
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
    <p>{{ body }}</p>

    <label class="scope-toggle">
      <input
        v-model="scope"
        type="checkbox"
        true-value="all"
        false-value="stale"
        class="switch"
      />
      Everything, not just what looks stale
    </label>
    <p class="text-caption text--secondary">
      {{
        scope === "all"
          ? "Ignores the recorded provenance - use this when you edited a source the provenance can't see."
          : "Only what the recorded provenance says needs doing."
      }}
    </p>

    <template v-if="hasWork">
      <template v-if="showsBuilds">
        <p class="text-caption text--secondary">Will build:</p>
        <div v-if="buildTargets.length" class="detail-block">
          <div v-for="target in buildTargets" :key="target.name">
            <strong>{{ target.name }}</strong>
            <span class="text-caption text--secondary">{{
              target.artifact.label
            }}</span>
          </div>
        </div>
        <p v-else class="text--disabled text-caption">Nothing to build.</p>
      </template>

      <template v-if="showsFlashes">
        <p class="text-caption text--secondary">Will flash:</p>
        <div v-if="flashTargets.length" class="detail-block">
          <div v-for="entry in flashTargets" :key="entry.id">
            <strong>{{ entry.name ?? entry.type }}</strong>
            <span class="text-caption text--secondary"
              >{{ entry.type }} · {{ entry.id }}</span
            >
          </div>
        </div>
        <p v-else class="text--disabled text-caption">Nothing to flash.</p>
      </template>
    </template>
    <p v-else class="alert alert--info">Nothing for this to do right now.</p>

    <p class="text-caption text--disabled">
      This is a preview only - the agent re-decides what to touch when the call
      actually arrives.
      <template v-if="operation === 'update_all'">
        The flash list above is a floor, not a forecast: a build can only add
        boards to it.
      </template>
    </p>

    <p v-if="writesToBoards" class="alert alert--warning">
      This stops Klipper and writes to hardware. Do not interrupt it once
      started.
    </p>
    <p v-if="busy" class="alert alert--error">{{ busyMessage }}</p>

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

<style scoped>
.scope-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 2px;
}

.detail-block {
  margin: 2px 0 10px;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
  max-height: 200px;
  overflow-y: auto;
}

.detail-block > div + div {
  margin-top: 4px;
}

.detail-block strong {
  margin-right: 6px;
}
</style>
