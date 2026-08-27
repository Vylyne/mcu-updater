<script setup lang="ts">
// The one job the agent ever runs at a time (docs/agent-api.md's "Jobs":
// "One job at a time, and it is not a queue"), rendered from store state
// that the event handlers in store/agent.ts already keep current.
import { computed, reactive, ref } from "vue";
import { adoptSerial, cancelJob, state } from "../store/agent";
import { cancelIsImmediate } from "../api/jobs";
import UiPanel from "./UiPanel.vue";

const cancelling = ref(false);
const adopting = reactive<Record<string, boolean>>({});

// add_mcu's own result - see flash.py's add_mcu_start. `candidates` are
// boards that appeared and are not yet in the registry; `already_tracked`
// appeared but the registry already knows them (a re-bootloadered board),
// so there is nothing left to adopt for those.
interface AddMcuResult {
  type: string;
  candidates: { serial: string; path: string; state: string }[];
  already_tracked: { serial: string; path: string; state: string }[];
}

const addMcuResult = computed<AddMcuResult | null>(() => {
  if (state.job?.kind !== "add_mcu" || state.job.state !== "succeeded") {
    return null;
  }
  return (state.job.result as AddMcuResult | undefined) ?? null;
});

async function adoptCandidate(serial: string, type: string): Promise<void> {
  adopting[serial] = true;
  await adoptSerial(type, serial);
  adopting[serial] = false;
}

const progressText = computed(() => {
  const progress = state.job?.progress;
  if (!progress) return null;
  // `index` is 0-based on the wire.
  return `${progress.step} (${progress.index + 1}/${progress.total})`;
});

const progressPercent = computed(() => {
  const progress = state.job?.progress;
  if (!progress || progress.total <= 0) return null;
  return Math.min(100, ((progress.index + 1) / progress.total) * 100);
});

const cancelWording = computed(() => {
  if (!state.job) return "";
  return cancelIsImmediate(state.job.kind)
    ? "This stops the current step immediately."
    : "This will cancel after the current board or build finishes - " +
        "interrupting a write mid-way leaves it half-written.";
});

const showCancel = computed(
  () =>
    state.job !== null &&
    (state.job.state === "queued" || state.job.state === "running") &&
    !state.job.cancel_requested,
);

async function onCancel(): Promise<void> {
  cancelling.value = true;
  await cancelJob();
  cancelling.value = false;
}
</script>

<template>
  <UiPanel v-if="state.job" :title="`Job: ${state.job.kind}`">
    <template #buttons>
      <button
        v-if="showCancel"
        type="button"
        :disabled="cancelling"
        @click="onCancel"
      >
        Cancel
      </button>
    </template>

    <p class="text-caption text--secondary">state: {{ state.job.state }}</p>

    <div v-if="progressPercent !== null" class="progress-track">
      <div class="progress-bar" :style="{ width: `${progressPercent}%` }" />
    </div>
    <p v-if="progressText" class="text-caption">
      {{ progressText }}
    </p>

    <p v-if="state.job.error" class="alert alert--error">
      {{ state.job.error.code }} - {{ state.job.error.message }}
    </p>

    <p v-if="state.job.cancel_requested" class="alert alert--info">
      Cancelling… {{ cancelWording }}
    </p>
    <p v-else-if="showCancel" class="muted">
      {{ cancelWording }}
    </p>

    <div v-if="addMcuResult && addMcuResult.candidates.length" class="picker">
      <p>New board(s) appeared and are not tracked yet:</p>
      <ul class="devices">
        <li
          v-for="candidate in addMcuResult.candidates"
          :key="candidate.serial"
        >
          <span>{{ candidate.path }}</span>
          <button
            type="button"
            :disabled="adopting[candidate.serial]"
            @click="adoptCandidate(candidate.serial, addMcuResult.type)"
          >
            {{ adopting[candidate.serial] ? "Adopting…" : "Adopt" }}
          </button>
        </li>
      </ul>
    </div>
    <p
      v-else-if="addMcuResult && !addMcuResult.already_tracked.length"
      class="muted"
    >
      No board appeared. Check /dev/serial/by-id/ - if it is there, adopt it
      directly once bus devices show it.
    </p>
    <p v-else-if="addMcuResult" class="muted">
      The board that appeared is already tracked - nothing to adopt. Flash
      Klipper onto it when ready.
    </p>

    <div v-if="state.log && state.log.job_id === state.job.id" class="log">
      <p v-if="state.logOmitted" class="muted">
        Some earlier lines were dropped from the buffer and are not shown.
      </p>
      <pre class="detail-block"><span
        v-for="line in state.log.lines"
        :key="line.i"
        :data-stream="line.s"
        >{{ line.t }}
</span></pre>
    </div>
  </UiPanel>
</template>

<style scoped>
.progress-track {
  height: 4px;
  border-radius: 2px;
  background: var(--color-divider);
  overflow: hidden;
  margin-bottom: 4px;
}

.progress-bar {
  height: 100%;
  background: var(--color-primary);
  transition: width 200ms ease;
}

.detail-block {
  margin: 2px 0 6px;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
  user-select: text;
  white-space: pre-wrap;
  font-size: 0.75rem;
  max-height: 320px;
  overflow: auto;
}
</style>
