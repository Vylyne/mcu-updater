<script setup lang="ts">
// The one job the agent ever runs at a time (docs/agent-api.md's "Jobs":
// "One job at a time, and it is not a queue"), rendered from store state
// that the event handlers in store/agent.ts already keep current.
import { computed, reactive, ref } from "vue";
import { adoptSerial, cancelJob, state } from "../store/agent";
import { cancelIsImmediate } from "../api/jobs";

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

const cancelWording = computed(() => {
  if (!state.job) return "";
  return cancelIsImmediate(state.job.kind)
    ? "This stops the current step immediately."
    : "This will cancel after the current board or build finishes - " +
        "interrupting a write mid-way leaves it half-written.";
});

async function onCancel(): Promise<void> {
  cancelling.value = true;
  await cancelJob();
  cancelling.value = false;
}
</script>

<template>
  <section v-if="state.job" class="job">
    <h2>Job: {{ state.job.kind }}</h2>
    <p>state: {{ state.job.state }}</p>
    <p v-if="progressText">{{ progressText }}</p>
    <p v-if="state.job.error">
      error: {{ state.job.error.code }} - {{ state.job.error.message }}
    </p>

    <template
      v-if="state.job.state === 'queued' || state.job.state === 'running'"
    >
      <p v-if="state.job.cancel_requested">Cancelling… {{ cancelWording }}</p>
      <template v-else>
        <button type="button" :disabled="cancelling" @click="onCancel">
          Cancel
        </button>
        <p class="muted">{{ cancelWording }}</p>
      </template>
    </template>

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
      <pre><span
        v-for="line in state.log.lines"
        :key="line.i"
        :data-stream="line.s"
        >{{ line.t }}
</span></pre>
    </div>
  </section>
</template>
