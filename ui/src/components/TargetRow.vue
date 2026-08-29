<script setup lang="ts">
// One targets[] row, rendered the same way for an MCU or a display - see
// docs/decisions.md and docs/agent-api.md's "targets" section for why the
// two are one shape. Actions ride on the same row's own actions[] /
// devices[].actions[] - ActionButton.vue is the {id, label, method, params,
// blocked, choices?} renderer, this file just supplies preview devices and
// the transient busy gate a payload never carries.
//
// Layout mirrors FirmwareUpdaterPanelTarget.vue in the Mainsail fork: a
// header line (name, descriptor, module version, device count, spacer,
// artifact chip, profile chip, actions, overflow menu), then one sub-row per
// device (state icon, identity, spacer, version, verdict, device actions,
// detail expander), then a trailing divider.
import { computed, nextTick, ref, watch } from "vue";
import { flipMenuIfOffscreen, useClickOutsideToClose } from "../clickOutside";
import {
  fetchTargetDetail,
  hasCapability,
  lockedBy,
  removeType,
  state,
} from "../store/agent";
import type { Target, TargetDevice } from "../api/targets";
import { devicesToFlash } from "../api/bulk";
import ActionButton from "./ActionButton.vue";
import UiIcon from "./UiIcon.vue";
import UiDialog from "./UiDialog.vue";
import TypeDialog from "./TypeDialog.vue";
import type { Family } from "../api/mcutype";
import {
  mdiAlertOutline,
  mdiCheckCircleOutline,
  mdiCloseCircleOutline,
  mdiDotsVertical,
  mdiHelpCircleOutline,
  mdiProgressWrench,
  mdiTuneVariant,
} from "../icons";

const props = defineProps<{ target: Target }>();

const expanded = ref(false);
const loading = ref(false);
const detail = ref<Record<string, unknown> | null>(null);
const menuOpen = ref(false);
const menuRef = ref<HTMLElement | null>(null);
useClickOutsideToClose(menuRef, menuOpen);
watch(menuOpen, (open) => {
  if (open) void nextTick(() => flipMenuIfOffscreen(menuRef.value));
});
const typeDialogOpen = ref(false);
const removing = ref(false);

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

const deviceSummary = computed(() => {
  const present = props.target.devices.filter((d) => d.present).length;
  return `${present}/${props.target.devices.length} present`;
});

// Actions are gated on `blocked` from the payload plus this transient state
// on top - docs/agent-api.md is explicit that a job already running belongs
// to `job`/`locked_by`, not to `blocked`, so it is not baked into the row.
// `locked_by` (a CLI build/flash holding the host lock) gates the same way a
// job of ours does: either way a new one would be refused.
const busyReason = computed(() => {
  const lock = lockedBy();
  if (lock) return lock.label;
  const job = state.job;
  if (!job || (job.state !== "queued" && job.state !== "running")) return null;
  return `${job.kind} is already running`;
});

/** Devices a flash action on this row would actually write, for
 * ActionButton's confirmation prompt - delegates to api/bulk.ts's
 * devicesToFlash so a single-row preview and a fleet-wide one can never
 * disagree about what "needs flashing" means. */
function previewFor(action: Target["actions"][number]): TargetDevice[] {
  const scope = (action.params.scope as string | undefined) ?? "stale";
  return devicesToFlash(props.target, scope === "all" ? "all" : "stale");
}

/** The ones that belong in the header itself; everything else goes in the
 * overflow menu - same split, and same reasoning, as
 * FirmwareUpdaterPanelTarget.HEADER_ACTIONS: a board with no profile yet
 * shows a blocked Build right beside the thing that unblocks it, rather than
 * burying it a click away. */
const HEADER_ACTION_ORDER = ["build", "profile", "flash"];

const headerActions = computed(() =>
  props.target.actions
    .filter((a) => HEADER_ACTION_ORDER.includes(a.id))
    .sort(
      (a, b) =>
        HEADER_ACTION_ORDER.indexOf(a.id) - HEADER_ACTION_ORDER.indexOf(b.id),
    ),
);

const menuActions = computed(() =>
  props.target.actions.filter((a) => !HEADER_ACTION_ORDER.includes(a.id)),
);

function wants(action: Target["actions"][number]): boolean {
  return action.id === "flash" && props.target.needs_flash === true;
}

// The vendor updated the profile this config came from, and the saved
// answers still match what the profile put there (reason === "seed_moved") -
// a customised config is never offered this, and the agent refuses it there
// anyway. ActionButton never learns what a profile is; this is the one place
// that does, per its own doc comment.
function offersReseed(action: Target["actions"][number]): boolean {
  return action.id === "build" && props.target.profile?.reason === "seed_moved";
}

const reseedDefault = computed(
  () =>
    (state.status?.settings as { reseed_on_build?: boolean } | undefined)
      ?.reseed_on_build !== false,
);

/** The profile chip, or nothing - nothing for a display (no answers to
 * seed) and nothing for an unmanaged type (every type predating profiles).
 * A moved seed names the profile rather than saying "profile updated",
 * mirroring FirmwareUpdaterPanelTarget.vue's profileChip getter. */
const profileChip = computed(() => {
  const profile = props.target.profile;
  if (!profile || !profile.managed) return null;
  if (profile.reason === "seed_moved" && profile.profile) {
    return { text: `${profile.profile} updated`, tone: profile.tone };
  }
  return { text: profile.label, tone: profile.tone };
});

const profileHint = computed(() => {
  const profile = props.target.profile;
  if (!profile) return "";
  const parent = profile.custom ? profile.parent : profile.profile;
  if (profile.reason === "customised" || profile.custom) {
    return parent ? `Forked from ${parent}` : "Your own answers";
  }
  return profile.profile
    ? `${profile.label} - ${profile.profile}`
    : profile.label;
});

// MCU-type management (fw.type.add/.update/.remove) applies to a
// kconfig_make target only - a display has no registry entry of this kind
// to edit. Kept here rather than a provider branch on the row's rendering:
// this is the one place docs/standalone-ui.md records as "still unscheduled"
// before this phase, and it stays additive - two extra menu rows, not a
// change to how the row itself renders.
const canManageType = computed(
  () =>
    props.target.provider === "kconfig_make" &&
    hasCapability("fw.type.add") &&
    hasCapability("fw.type.update") &&
    hasCapability("fw.type.remove"),
);

const families = computed(
  () => (state.status?.firmware_families as Family[] | undefined) ?? [],
);

function openEditType(): void {
  typeDialogOpen.value = true;
  menuOpen.value = false;
}

const removeConfirming = ref(false);
// Set from a type_has_serials refusal's data.data.serials - registry.py
// refuses removing a type that still tracks boards unless forced, since that
// is far more often a misclick than an intention.
const removeConflictSerials = ref<string[] | null>(null);

function openRemoveType(): void {
  removeConflictSerials.value = null;
  removeConfirming.value = true;
  menuOpen.value = false;
}

async function doRemoveType(force = false): Promise<void> {
  removing.value = true;
  const ok = await removeType(props.target.name, force);
  removing.value = false;
  if (ok) {
    removeConfirming.value = false;
    return;
  }
  const data = state.error?.data as { serials?: string[] } | undefined;
  removeConflictSerials.value = data?.serials ?? null;
}

function stateIcon(device: TargetDevice): string {
  if (device.state === "klipper" || device.state === "online") {
    return mdiCheckCircleOutline;
  }
  if (device.state === "katapult") return mdiProgressWrench;
  if (device.state === "silent") return mdiAlertOutline;
  return mdiHelpCircleOutline;
}

/** git describe is long; the tail is what differs. */
function shortVersion(version: string): string {
  const parts = version.split("-");
  return parts.length > 2 ? parts.slice(1, 3).join("-") : version;
}

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
  <article class="target-row" :data-tone="target.artifact.tone">
    <div class="target-header">
      <span class="target-name">{{ target.name }}</span>
      <span class="chip chip--outlined">{{ target.provider }}</span>
      <span
        v-if="target.descriptor !== target.name"
        class="text-caption text--disabled"
      >
        {{ target.descriptor }}
      </span>
      <span
        v-if="target.extra?.module_version"
        class="text-caption text--disabled"
      >
        {{ target.extra.module_version }}
      </span>
      <span class="text-caption text--disabled">{{ deviceSummary }}</span>

      <span class="spacer" />

      <span
        class="chip"
        :data-tone="target.artifact.tone"
        :title="target.artifact.label"
      >
        {{ target.artifact.label }}
      </span>

      <span
        v-if="profileChip"
        class="chip chip--outlined"
        :data-tone="profileChip.tone"
        :title="profileHint"
      >
        {{ profileChip.text }}
      </span>

      <ActionButton
        v-for="action in headerActions"
        :key="action.id"
        :action="action"
        :disabled="busyReason !== null"
        :disabled-reason="busyReason"
        :preview-devices="previewFor(action)"
        :wanted="wants(action)"
        :offers-reseed="offersReseed(action)"
        :reseed-default="reseedDefault"
      />

      <span
        v-if="menuActions.length || canManageType"
        ref="menuRef"
        class="target-menu"
      >
        <button
          type="button"
          class="btn-icon btn-icon--small"
          aria-label="More actions"
          @click="menuOpen = !menuOpen"
        >
          <UiIcon :path="mdiDotsVertical" size="x-small" />
        </button>
        <div v-if="menuOpen" class="menu-list">
          <ActionButton
            v-for="action in menuActions"
            :key="action.id"
            :action="action"
            variant="text"
            :disabled="busyReason !== null"
            :disabled-reason="busyReason"
            :preview-devices="previewFor(action)"
          />
          <template v-if="canManageType">
            <hr v-if="menuActions.length" class="divider" />
            <button type="button" class="menu-item" @click="openEditType">
              <UiIcon :path="mdiTuneVariant" size="x-small" />
              Edit type…
            </button>
            <button
              type="button"
              class="menu-item menu-item--danger"
              @click="openRemoveType"
            >
              <UiIcon :path="mdiCloseCircleOutline" size="x-small" />
              Remove type…
            </button>
          </template>
        </div>
      </span>
    </div>

    <ul v-if="target.devices.length" class="devices">
      <li
        v-for="device in target.devices"
        :key="device.id"
        class="device-row"
        :data-tone="device.tone"
      >
        <UiIcon :path="stateIcon(device)" size="x-small" :tone="device.tone" />
        <span class="device-identity">
          <span class="text--secondary">{{ device.id }}</span>
          <span v-if="device.name" class="text--disabled text-caption">{{
            device.name
          }}</span>
        </span>
        <!-- No separate "not present" flag: the agent's own `label` already
             says "Not connected" for exactly that case, and rendering both
             put a second, ragged column in the middle of the row saying the
             same thing twice. -->
        <span class="spacer" />
        <span v-if="device.version" class="text--disabled text-caption mr">
          {{ shortVersion(device.version) }}
        </span>
        <span class="text-caption" :data-tone="device.tone">{{
          device.label
        }}</span>
        <ActionButton
          v-for="action in device.actions"
          :key="action.id"
          :action="action"
          :disabled="busyReason !== null"
          :disabled-reason="busyReason"
          :preview-devices="[device]"
        />
      </li>
    </ul>
    <p v-else class="muted">
      {{ noDevicesHint }}
    </p>

    <button type="button" class="detail-toggle text-caption" @click="toggle">
      {{ expanded ? "Hide detail" : "Show detail" }}
    </button>
    <p v-if="expanded && loading">Loading…</p>
    <pre v-else-if="expanded && detail" class="detail-block">{{
      detailText
    }}</pre>

    <hr class="divider" />

    <TypeDialog
      v-if="typeDialogOpen"
      :type-name="target.name"
      :existing-names="[]"
      :families="families"
      @close="typeDialogOpen = false"
    />

    <UiDialog
      v-if="removeConfirming"
      title="Remove type"
      @close="removeConfirming = false"
    >
      <template v-if="removeConflictSerials">
        <p>
          '{{ target.name }}' still tracks {{ removeConflictSerials.length }}
          board(s):
        </p>
        <ul class="devices">
          <li v-for="serial in removeConflictSerials" :key="serial">
            {{ serial }}
          </li>
        </ul>
        <p class="muted">
          Remove them first, or confirm to remove the type and its serials
          together.
        </p>
      </template>
      <p v-else>
        Removes the registry section only. The saved menuconfig answers for '{{
          target.name
        }}' stay on disk - re-adding the same name restores them.
      </p>
      <template #actions>
        <button type="button" @click="removeConfirming = false">Cancel</button>
        <button
          type="button"
          class="btn-danger"
          :disabled="removing"
          @click="doRemoveType(removeConflictSerials !== null)"
        >
          {{ removing ? "Working…" : "Remove" }}
        </button>
      </template>
    </UiDialog>
  </article>
</template>

<style scoped>
/* :data-tone on the <article> itself is only there for the bare
   [data-tone="ok"] etc. rule in style.css to match against - it was never
   meant to colour the whole row. Without an explicit colour here, color's
   default inheritance carries that tone down into every child that doesn't
   set its own - the row's name, and every .btn-icon (which explicitly does
   `color: inherit`), same leak style.css's .menu-list had before. */
.target-row {
  color: var(--color-text);
}

.target-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}

.target-name {
  font-weight: 500;
}

.target-menu {
  position: relative;
}

/* Same reasoning as .target-row above: :data-tone here is for the verdict
   span's own bare [data-tone] match, not for colouring the whole row - the
   device's state icon and its own action icons would otherwise inherit it. */
.device-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 0.8rem;
  color: var(--color-text);
}

/* A by-id serial and a /dev/serial/by-path name are both long enough to wrap
   the row without this. */
.device-identity {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 50%;
}

.mr {
  margin-right: 4px;
}

.detail-toggle {
  background: none;
  border: none;
  color: var(--color-primary);
  cursor: pointer;
  padding: 4px 0;
}

.detail-block {
  margin: 2px 0 6px;
  padding: 6px 8px;
  border-radius: 4px;
  background-color: var(--color-inset);
  user-select: text;
  white-space: pre-wrap;
  font-size: 0.75rem;
}
</style>
