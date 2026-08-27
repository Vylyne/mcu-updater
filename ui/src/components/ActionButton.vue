<script setup lang="ts">
// One entry from a target's or a device's `actions[]`. `blocked` and
// `disabled` are deliberately separate props: `blocked` is the payload's own
// verdict (same {code, message, data} shape a failed call carries -
// docs/agent-api.md's "targets" section), while `disabled`/`disabledReason`
// come from transient state - a job already running - which the payload
// deliberately excludes ("that is what `job` and `locked_by` are"). Both
// gate the same button, but only one of them is this row's business to know.
import { computed, ref } from "vue";
import { fetchChoices, invokeAction, openKconfig, state } from "../store/agent";
import type { Action } from "../api/targets";

const props = defineProps<{
  action: Action;
  disabled?: boolean;
  disabledReason?: string | null;
  /** Devices this action would actually write, for a confirmation prompt on
   * a flashing method - never re-derived from a guess, always the caller's
   * own targets[]/devices[] data. Absent for a non-destructive action. */
  previewDevices?: { id: string; name: string | null }[];
}>();

// Exactly one place naming which methods write to hardware, so a "Flash"
// label can never reach fw.flash/fw.flash_all without this prompt - and so
// tests/test_ui_contract.py's fw.* scan sees both literals.
const DESTRUCTIVE_METHODS = new Set(["fw.flash", "fw.flash_all"]);

// "Configure {family}" opens a session in KconfigDialog.vue rather than
// firing a normal request-and-done call - a Kconfig parse leaves a
// server-side session behind, and the dialog is what talks to it from here.
const KCONFIG_OPEN_METHOD = "fw.kconfig.open";
const isKconfigOpen = computed(
  () => props.action.method === KCONFIG_OPEN_METHOD,
);

const isBlocked = computed(() => props.action.blocked !== null);
const isDisabled = computed(() => isBlocked.value || props.disabled === true);
const blockedMessage = computed(
  () => props.action.blocked?.message ?? props.disabledReason ?? null,
);
const isDestructive = computed(() =>
  DESTRUCTIVE_METHODS.has(props.action.method),
);

const confirming = ref(false);
const pickingChoice = ref(false);
const choiceOptions = ref<{ name: string; hint: string }[] | null>(null);
const choiceLoading = ref(false);
const running = ref(false);
const kconfigConflict = ref(false);

function optionHint(entry: unknown): string {
  const distinguishing = (entry as { distinguishing?: unknown }).distinguishing;
  if (!Array.isArray(distinguishing) || distinguishing.length === 0) return "";
  return distinguishing
    .map((d) => (d as { label?: string }).label)
    .filter((label): label is string => typeof label === "string")
    .join(", ");
}

async function openChoices(): Promise<void> {
  const choices = props.action.choices;
  if (!choices) return;
  pickingChoice.value = true;
  choiceLoading.value = true;
  const result = await fetchChoices(choices.method, choices.params);
  const available = (result?.available as unknown[] | undefined) ?? [];
  choiceOptions.value = available.map((entry) => ({
    name: (entry as { name: string }).name,
    hint: optionHint(entry),
  }));
  choiceLoading.value = false;
}

async function pick(name: string): Promise<void> {
  const choices = props.action.choices;
  if (!choices) return;
  running.value = true;
  await invokeAction(props.action, { [choices.param]: name });
  running.value = false;
  pickingChoice.value = false;
  choiceOptions.value = null;
}

async function run(): Promise<void> {
  running.value = true;
  await invokeAction(props.action);
  running.value = false;
  confirming.value = false;
}

/** `force` retries after a kconfig_session_conflict refusal - another tab
 * left this same type/family with unsaved changes. */
async function openConfigure(force = false): Promise<void> {
  running.value = true;
  kconfigConflict.value = false;
  const ok = await openKconfig(
    props.action.params.name as string,
    props.action.params.fw as string,
    force,
  );
  running.value = false;
  if (!ok) {
    kconfigConflict.value = state.error?.code === "kconfig_session_conflict";
  }
}

function onClick(): void {
  if (isDisabled.value) return;
  if (isKconfigOpen.value) {
    void openConfigure(false);
    return;
  }
  if (props.action.choices) {
    void openChoices();
    return;
  }
  if (isDestructive.value) {
    confirming.value = true;
    return;
  }
  void run();
}
</script>

<template>
  <span class="action">
    <button
      type="button"
      :disabled="isDisabled || running"
      :title="blockedMessage ?? undefined"
      @click="onClick"
    >
      {{ running ? "Working…" : action.label }}
    </button>

    <span v-if="blockedMessage" class="muted">{{ blockedMessage }}</span>

    <span v-if="pickingChoice" class="picker">
      <p v-if="choiceLoading">Loading options…</p>
      <ul v-else-if="choiceOptions">
        <li v-for="option in choiceOptions" :key="option.name">
          <button type="button" :disabled="running" @click="pick(option.name)">
            {{ option.name }}
          </button>
          <span v-if="option.hint" class="muted">{{ option.hint }}</span>
        </li>
      </ul>
      <button type="button" @click="pickingChoice = false">Cancel</button>
    </span>

    <span v-if="kconfigConflict" class="picker">
      <p>
        Another session has unsaved changes to this configuration. Opening a
        second one risks one save discarding the other's work.
      </p>
      <button type="button" :disabled="running" @click="openConfigure(true)">
        Take over anyway
      </button>
      <button type="button" @click="kconfigConflict = false">Cancel</button>
    </span>

    <span v-if="confirming" class="picker">
      <p>
        {{ action.label }} will write to:
        <template v-if="previewDevices && previewDevices.length">
          <strong v-for="device in previewDevices" :key="device.id">{{
            device.name ?? device.id
          }}</strong>
        </template>
        <template v-else
          >an unknown set of devices - refusing to guess</template
        >
      </p>
      <button
        type="button"
        :disabled="!previewDevices || previewDevices.length === 0"
        @click="run"
      >
        Confirm
      </button>
      <button type="button" @click="confirming = false">Cancel</button>
    </span>
  </span>
</template>
