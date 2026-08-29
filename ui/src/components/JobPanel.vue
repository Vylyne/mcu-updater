<script setup lang="ts">
// The one job the agent ever runs at a time (docs/agent-api.md's "Jobs":
// "One job at a time, and it is not a queue"), rendered from store state
// that the event handlers in store/agent.ts already keep current.
import { computed, nextTick, reactive, ref, watch } from "vue";
import { adoptSerial, cancelJob, state } from "../store/agent";
import { cancelIsImmediate } from "../api/jobs";
import UiPanel from "./UiPanel.vue";

const panelRef = ref<InstanceType<typeof UiPanel> | null>(null);
const panelRoot = ref<HTMLElement | null>(null);
const logEl = ref<HTMLElement | null>(null);

// Sticky-scroll: pin the log to its bottom as lines arrive, but only while
// the reader hasn't scrolled up to look at something earlier. Reset to
// pinned on each new job, since a fresh log has nothing worth holding a
// scroll position on yet.
const stickToBottom = ref(true);

// Set when a new job starts, before its log block exists (state.log is
// still null or the previous job's). The job-id watch can't scrollIntoView
// against a panel that hasn't grown yet, so it defers the actual scroll to
// the lines-length watch below, which fires once real content has rendered.
const pendingScroll = ref(false);

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 24;
}

function onLogScroll(): void {
  if (logEl.value) stickToBottom.value = isAtBottom(logEl.value);
}

watch(
  () => state.log?.lines.length,
  () => {
    if (!stickToBottom.value) return;
    void nextTick(() => {
      if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight;
      // The deferred scroll-into-view from a fresh job: the log block has
      // now actually rendered with real content, so the panel has grown to
      // its real height and this scroll targets a stable layout.
      if (pendingScroll.value && state.log?.job_id === state.job?.id) {
        panelRoot.value?.scrollIntoView?.({
          behavior: "smooth",
          block: "start",
        });
        pendingScroll.value = false;
      }
    });
  },
);

// A job "starting" is its id changing to something new - bring the panel
// into view and open it even if the reader left it collapsed, since a
// collapsed job panel with no visible progress reads as a hang.
watch(
  () => state.job?.id,
  (id, previousId) => {
    if (!id || id === previousId) return;
    stickToBottom.value = true;
    panelRef.value?.expand();
    pendingScroll.value = true;
    void nextTick(() => {
      // jsdom (unit tests) has no scrollIntoView implementation at all.
      // The log block for a brand-new job isn't rendered yet at this point
      // (state.log is still null or the previous job's), so this only gets
      // the panel to its shortest layout - the real scroll-into-view is
      // deferred to the lines-length watch above, once real content lands.
      panelRoot.value?.scrollIntoView?.({
        behavior: "smooth",
        block: "start",
      });
      if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight;
    });
  },
);

interface BulkFailure {
  type?: string;
  id?: string;
  error?: string;
}

/** `result.*.failures` from a build_all/flash_all/update_all job -
 * agent-api.md is explicit that a job with failures still ends `succeeded`,
 * and that this is the thing to render, not the job state: "a batch that
 * drops something and reports success is the failure this whole area exists
 * to make impossible". build_all/flash_all carry `failures` at the top of
 * `result`; update_all nests it under `build`/`flash` since it composes both. */
const failures = computed<BulkFailure[]>(() => {
  const result = state.job?.result as Record<string, unknown> | undefined;
  if (!result) return [];
  const collected: BulkFailure[] = [];
  if (Array.isArray(result.failures)) {
    collected.push(...(result.failures as BulkFailure[]));
  }
  for (const key of ["build", "flash"] as const) {
    const half = result[key] as { failures?: BulkFailure[] } | undefined;
    if (Array.isArray(half?.failures)) collected.push(...half.failures);
  }
  return collected;
});

// Klipper reruns olddefconfig when src/Kconfig is newer than the saved
// .config, which silently rewrites menuconfig answers - invisible unless
// this is checked explicitly. Only a single fw.build job's result carries
// this key; other kinds simply don't have it.
const configRewritten = computed(
  () =>
    (state.job?.result as { config_rewritten?: boolean } | undefined)
      ?.config_rewritten === true,
);

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
  <div v-if="state.job" ref="panelRoot">
    <UiPanel
      ref="panelRef"
      :title="`Job: ${state.job.kind}`"
      collapsible
      storage-key="job"
    >
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

      <!-- A bulk job with failures still ends "succeeded" - the state alone
         would read as a clean run, so this renders regardless of state. -->
      <div v-if="failures.length" class="alert alert--error">
        <div>
          <strong>{{ failures.length }}</strong> of the batch failed:
        </div>
        <div v-for="(failure, index) in failures" :key="index">
          {{ failure.type ?? failure.id ?? "?" }} - {{ failure.error }}
        </div>
      </div>

      <div v-if="state.bulkSkipped.length" class="alert alert--warning">
        <div>
          {{ state.bulkSkipped.length }} target(s) could not be touched:
        </div>
        <div v-for="(entry, index) in state.bulkSkipped" :key="index">
          {{ entry.type }} - {{ entry.reason }}
        </div>
      </div>

      <p v-if="configRewritten" class="alert alert--warning">
        Klipper rewrote the saved config for this build (olddefconfig ran
        because src/Kconfig changed) - some menuconfig answers may have moved.
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
        <pre ref="logEl" class="detail-block" @scroll="onLogScroll"><span
        v-for="line in state.log.lines"
        :key="line.i"
        :data-stream="line.s"
        >{{ line.t }}
</span></pre>
      </div>
    </UiPanel>
  </div>
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

/* stdout_warn/stdout_error are classified subprocess output (build.py's
   classify_output); warn/error/info/cmd are the agent's own messages. Plain
   stdout lines get no rule and inherit the default text color. */
[data-stream="error"],
[data-stream="stdout_error"] {
  color: var(--color-error);
}

[data-stream="warn"],
[data-stream="stdout_warn"] {
  color: var(--tone-attention);
}

[data-stream="info"] {
  color: var(--color-text-secondary);
}

[data-stream="cmd"] {
  color: var(--color-primary);
  font-weight: 600;
}
</style>
