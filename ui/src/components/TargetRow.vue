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
import { computed, ref } from "vue";
import { fetchTargetDetail, state } from "../store/agent";
import type { Target, TargetDevice } from "../api/targets";
import ActionButton from "./ActionButton.vue";
import UiIcon from "./UiIcon.vue";
import {
  mdiAlertOutline,
  mdiCheckCircleOutline,
  mdiDotsVertical,
  mdiHelpCircleOutline,
  mdiProgressWrench,
} from "../icons";

const props = defineProps<{ target: Target }>();

const expanded = ref(false);
const loading = ref(false);
const detail = ref<Record<string, unknown> | null>(null);
const menuOpen = ref(false);

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
const busyReason = computed(() => {
  const job = state.job;
  if (!job || (job.state !== "queued" && job.state !== "running")) return null;
  return `${job.kind} is already running`;
});

/** Devices a flash action on this row would actually write, for
 * ActionButton's confirmation prompt. `scope` (default "stale") comes off
 * the action's own params, mirroring the agent's own selection in
 * bulk.py's _boards_to_flash/_screens_to_flash - "all" means every present
 * device, otherwise only the ones the payload already marked needing one. */
function previewFor(action: Target["actions"][number]): TargetDevice[] {
  const scope = (action.params.scope as string | undefined) ?? "stale";
  return props.target.devices.filter((device) =>
    scope === "all" ? device.present : device.needs_flash === true,
  );
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
      <span class="chip chip--outlined chip--x-small">{{
        target.provider
      }}</span>
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
        class="chip chip--x-small"
        :data-tone="target.artifact.tone"
        :title="target.artifact.label"
      >
        {{ target.artifact.label }}
      </span>

      <span
        v-if="profileChip"
        class="chip chip--x-small chip--outlined"
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
      />

      <span v-if="menuActions.length" class="target-menu">
        <button
          type="button"
          class="btn-icon btn-icon--small"
          aria-label="More actions"
          @click="menuOpen = !menuOpen"
        >
          <UiIcon :path="mdiDotsVertical" size="x-small" />
        </button>
        <div v-if="menuOpen" class="target-menu-list">
          <ActionButton
            v-for="action in menuActions"
            :key="action.id"
            :action="action"
            variant="text"
            :disabled="busyReason !== null"
            :disabled-reason="busyReason"
            :preview-devices="previewFor(action)"
          />
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
        <UiIcon :path="stateIcon(device)" size="x-small" />
        <span class="device-identity">
          <span class="text--secondary">{{ device.id }}</span>
          <span v-if="device.name" class="text--disabled text-caption">{{
            device.name
          }}</span>
        </span>
        <span v-if="!device.present" class="muted text-caption"
          >not present</span
        >
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
  </article>
</template>

<style scoped>
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

.target-menu-list {
  position: absolute;
  right: 0;
  top: 100%;
  z-index: 10;
  background: var(--color-surface);
  border-radius: 4px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  padding: 4px;
  display: flex;
  flex-direction: column;
  min-width: 160px;
}

.device-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 0.8rem;
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
