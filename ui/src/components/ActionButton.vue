<script setup lang="ts">
// One entry from a target's or a device's `actions[]`. `blocked` and
// `disabled` are deliberately separate props: `blocked` is the payload's own
// verdict (same {code, message, data} shape a failed call carries -
// docs/agent-api.md's "targets" section), while `disabled`/`disabledReason`
// come from transient state - a job already running - which the payload
// deliberately excludes ("that is what `job` and `locked_by` are"). Both
// gate the same button, but only one of them is this row's business to know.
import { computed, ref } from "vue";
import {
  fetchChoices,
  invokeAction,
  openKconfig,
  printerBusy,
  state,
} from "../store/agent";
import type { Action } from "../api/targets";
import UiIcon from "./UiIcon.vue";
import UiDialog from "./UiDialog.vue";
import {
  mdiCloseCircleOutline,
  mdiCogOutline,
  mdiFlash,
  mdiHammer,
  mdiTrayArrowUp,
  mdiTuneVariant,
  mdiUndoVariant,
  mdiUpdate,
} from "../icons";

const props = withDefaults(
  defineProps<{
    action: Action;
    disabled?: boolean;
    disabledReason?: string | null;
    /** Devices this action would actually write, for a confirmation prompt on
     * a flashing method - never re-derived from a guess, always the caller's
     * own targets[]/devices[] data. Absent for a non-destructive action. */
    previewDevices?: { id: string; name: string | null }[];
    /** "icon" (default) is a row's own [build]/[flash]/... buttons, matching
     * the fork panel's icon-only actions - "text" renders as a `.menu-item`
     * row, for the one context that currently uses it: TargetRow's own
     * overflow menu, alongside its "Edit type…"/"Remove type…" rows. */
    variant?: "icon" | "text";
    /** Whether this action currently *wants* doing, not just whether it
     * *can* be done - the same swap FirmwareUpdaterPanelTarget.vue makes for
     * flash's icon and colour. Ignored outside variant="icon". */
    wanted?: boolean;
    /** True for a `build` action on a target whose saved config still
     * matches its profile but the vendor's seed has moved
     * (profile.reason === "seed_moved") - the caller (TargetRow) knows what
     * a profile is so this component never has to. Offers a choice before
     * running rather than silently taking (or silently skipping) the
     * vendor's update. */
    offersReseed?: boolean;
    /** Preselected answer for the reseed prompt, from settings.reseed_on_build -
     * agreeing with what a CLI or fleet build would do by default. */
    reseedDefault?: boolean;
    /** True for a flash action whose params carry a `scope` (TargetRow's
     * type-level fw.flash_all, never a device-level fw.flash) - offers the
     * confirm dialog's "everything, not just what looks stale" switch,
     * mirroring BulkDialog.vue's global override. */
    offersOverride?: boolean;
    /** previewDevices recomputed at scope "all" - what the override switch
     * swaps the confirm dialog to when checked. */
    allPreviewDevices?: { id: string; name: string | null }[];
  }>(),
  {
    disabled: false,
    disabledReason: null,
    previewDevices: undefined,
    variant: "icon",
    wanted: false,
    offersReseed: false,
    reseedDefault: true,
    offersOverride: false,
    allPreviewDevices: undefined,
  },
);

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

// Untracking is reversible - it keeps the type's saved config and every
// built artifact, only the registry's serial entry goes away - so it gets
// its own reassuring confirm rather than either the flash confirm's warning
// framing or no confirm at all.
const isUntrack = computed(() => props.action.id === "untrack");

const confirming = ref(false);
const untrackConfirming = ref(false);
const reseedPrompting = ref(false);
const reseed = ref(true);
// Never latches - same reasoning as BulkDialog.vue's own scope ref: reopening
// this dialog with the last run's override still checked would make the
// deliberate, occasional choice the default for next time.
const overrideAll = ref(false);
const effectivePreview = computed(() =>
  overrideAll.value && props.allPreviewDevices
    ? props.allPreviewDevices
    : props.previewDevices,
);
const pickingChoice = ref(false);
const choiceOptions = ref<{ name: string; hint: string }[] | null>(null);
const choiceLoading = ref(false);
const running = ref(false);
const kconfigConflict = ref(false);

const busyMessage = computed(() =>
  state.status?.printing === true
    ? "The printer is printing - flashing would shut the MCU down mid-print."
    : "The printer is moving - flashing would shut the MCU down mid-motion.",
);

// Icons by action id, same table FirmwareUpdaterPanelTarget.vue keeps - an
// action id this row does not recognise falls back to a plain cog rather
// than a gap.
const ICONS: Record<string, string> = {
  build: mdiHammer,
  flash: mdiFlash,
  update: mdiUpdate,
  untrack: mdiCloseCircleOutline,
  profile: mdiTuneVariant,
  "profile:revert": mdiUndoVariant,
};

const icon = computed(() => {
  if (props.action.id === "flash")
    return props.wanted ? mdiTrayArrowUp : mdiFlash;
  if (props.action.id.startsWith("configure")) return mdiCogOutline;
  return ICONS[props.action.id] ?? mdiCogOutline;
});

const isPrimary = computed(() => {
  if (isBlocked.value) return false;
  if (props.action.id === "build") return true;
  if (props.action.id === "flash") return props.wanted;
  return false;
});

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

async function run(extra: Record<string, unknown> = {}): Promise<void> {
  running.value = true;
  await invokeAction(props.action, extra);
  running.value = false;
  confirming.value = false;
  untrackConfirming.value = false;
  reseedPrompting.value = false;
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
    overrideAll.value = false;
    confirming.value = true;
    return;
  }
  if (isUntrack.value) {
    untrackConfirming.value = true;
    return;
  }
  if (props.offersReseed) {
    reseed.value = props.reseedDefault;
    reseedPrompting.value = true;
    return;
  }
  void run();
}
</script>

<template>
  <span class="action">
    <button
      v-if="variant === 'icon'"
      type="button"
      class="btn-icon btn-icon--small"
      :class="{ 'btn-icon--primary': isPrimary }"
      :disabled="isDisabled || running"
      :title="blockedMessage ?? action.label"
      @click="onClick"
    >
      <UiIcon :path="icon" size="x-small" />
    </button>
    <button
      v-else
      type="button"
      class="menu-item"
      :class="{ 'menu-item--danger': isDestructive }"
      :disabled="isDisabled || running"
      :title="blockedMessage ?? undefined"
      @click="onClick"
    >
      <UiIcon :path="icon" size="x-small" />
      {{ running ? "Working…" : action.label }}
    </button>

    <span v-if="blockedMessage && variant === 'text'" class="muted">{{
      blockedMessage
    }}</span>

    <UiDialog
      v-if="pickingChoice"
      :title="action.label"
      @close="pickingChoice = false"
    >
      <p v-if="choiceLoading">Loading options…</p>
      <ul v-else-if="choiceOptions" class="devices">
        <li v-for="option in choiceOptions" :key="option.name">
          <button type="button" :disabled="running" @click="pick(option.name)">
            {{ option.name }}
          </button>
          <span v-if="option.hint" class="muted text-caption">{{
            option.hint
          }}</span>
        </li>
      </ul>
      <template #actions>
        <button type="button" @click="pickingChoice = false">Cancel</button>
      </template>
    </UiDialog>

    <UiDialog
      v-if="kconfigConflict"
      title="Configuration in use"
      @close="kconfigConflict = false"
    >
      <p>
        Another session has unsaved changes to this configuration. Opening a
        second one risks one save discarding the other's work.
      </p>
      <template #actions>
        <button type="button" @click="kconfigConflict = false">Cancel</button>
        <button type="button" :disabled="running" @click="openConfigure(true)">
          Take over anyway
        </button>
      </template>
    </UiDialog>

    <UiDialog
      v-if="confirming"
      :title="action.label"
      @close="confirming = false"
    >
      <p v-if="!effectivePreview || !effectivePreview.length">
        {{ action.label }} will write to an unknown set of devices - refusing
        to guess.
      </p>
      <template v-else>
        <p>{{ action.label }} will write to:</p>
        <ul class="devices">
          <li v-for="device in effectivePreview" :key="device.id">
            <strong>{{ device.name ?? device.id }}</strong>
          </li>
        </ul>
      </template>
      <label v-if="offersOverride" class="override-toggle">
        <input v-model="overrideAll" type="checkbox" class="switch" />
        Everything, not just what looks stale
      </label>
      <p v-if="printerBusy()" class="alert alert--error">{{ busyMessage }}</p>
      <template #actions>
        <button type="button" @click="confirming = false">Cancel</button>
        <button
          type="button"
          :disabled="
            !effectivePreview || effectivePreview.length === 0 || printerBusy()
          "
          @click="run(overrideAll ? { scope: 'all' } : {})"
        >
          Confirm
        </button>
      </template>
    </UiDialog>

    <UiDialog
      v-if="untrackConfirming"
      :title="action.label"
      @close="untrackConfirming = false"
    >
      <p>
        Stop tracking
        <strong v-if="previewDevices && previewDevices.length">{{
          previewDevices[0].name ?? previewDevices[0].id
        }}</strong>
        <template v-else>this board</template>?
      </p>
      <p class="alert alert--info">
        The board keeps its firmware, the type keeps its saved configuration and
        built artifacts, and re-adding it later makes it flashable again with
        nothing to rebuild.
      </p>
      <template #actions>
        <button type="button" @click="untrackConfirming = false">Cancel</button>
        <button type="button" :disabled="running" @click="run()">
          Confirm
        </button>
      </template>
    </UiDialog>

    <UiDialog
      v-if="reseedPrompting"
      :title="action.label"
      @close="reseedPrompting = false"
    >
      <p>
        This board's saved config still matches its profile, and the vendor's
        seed has moved since.
      </p>
      <label class="radio-row">
        <input v-model="reseed" type="radio" :value="true" />
        Take the vendor's update first
      </label>
      <label class="radio-row">
        <input v-model="reseed" type="radio" :value="false" />
        Build as is
      </label>
      <template #actions>
        <button type="button" @click="reseedPrompting = false">Cancel</button>
        <button type="button" :disabled="running" @click="run({ reseed })">
          {{ running ? "Working…" : action.label }}
        </button>
      </template>
    </UiDialog>
  </span>
</template>

<style scoped>
.radio-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
}

.override-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 2px;
}
</style>
