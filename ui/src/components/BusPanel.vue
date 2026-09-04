<script setup lang="ts">
// fw.status's `bus` - every serial device this host can see, whether or not
// a type claims it. `tracked_by: null` is docs/agent-api.md's "new board,
// want to track it?" case; this is where that gets a UI. See store/agent.ts's
// refreshStatus for why state.bus is not empty on first load.
//
// A device can also be `ignored` (fw.bus.ignore/.unignore) - flagged, not
// filtered server-side, so a mis-ignored board is always recoverable from the
// disclosure section below rather than only by hand-editing the cfg.
import {
  computed,
  nextTick,
  reactive,
  ref,
  watch,
  type ComponentPublicInstance,
} from "vue";
import { flipMenuIfOffscreen, useClickOutsideToClose } from "../clickOutside";
import {
  adoptSerial,
  adoptCanbus,
  clearRoadrunner,
  hasCapability,
  ignoreCanbus,
  ignoreSerial,
  provisionRoadrunner,
  state,
  unignoreCanbus,
  unignoreSerial,
} from "../store/agent";
import {
  isRoadrunnerDevice,
  roadrunnerDiagnosticUid,
  roadrunnerDisplaySerial,
  roadrunnerIdentityState,
  type BusDevice,
  type Target,
} from "../api/targets";
import type { CanbusDevice, CanbusScanDevice } from "../store/agent";
import type { Family } from "../api/mcutype";
import UiPanel from "./UiPanel.vue";
import UiIcon from "./UiIcon.vue";
import UiDialog from "./UiDialog.vue";
import TypeDialog from "./TypeDialog.vue";
import {
  mdiCloseCircleOutline,
  mdiDotsVertical,
  mdiHelpCircleOutline,
  mdiLan,
  mdiPlusCircleOutline,
  mdiUndoVariant,
  mdiUsb,
} from "../icons";

const devices = computed(() => state.bus as unknown as BusDevice[]);
const untracked = computed(() =>
  devices.value.filter((d) => !d.tracked_by && !d.ignored),
);
const ignored = computed(() => devices.value.filter((d) => d.ignored));
const canbusSightings = computed<CanbusDevice[]>(
  () =>
    (state.canbus?.devices as CanbusScanDevice[] | undefined)
      ?.filter((d) => !d.tracked_by)
      .map((d) => ({ ...d, kind: "can" as const })) ?? [],
);
const canbusDevices = computed(() =>
  canbusSightings.value.filter((d) => !d.ignored),
);
const ignoredCanbus = computed(() =>
  canbusSightings.value.filter((d) => d.ignored),
);
const ignoredCount = computed(
  () => ignored.value.length + ignoredCanbus.value.length,
);

// Only MCU types can adopt a serial (fw.serial.add) - a display is a
// separate provider with its own port config, not a bus device to claim.
const mcuTypeNames = computed(() => {
  const targets = (state.status?.targets as Target[] | undefined) ?? [];
  return targets
    .filter((t) => t.provider === "kconfig_make")
    .map((t) => t.name);
});

const canAdopt = computed(() => hasCapability("fw.serial.add"));
const canAdoptCan = computed(() => hasCapability("fw.canbus.add"));
const canIgnoreCan = computed(() => hasCapability("fw.canbus.ignore"));
const canUnignoreCan = computed(() => hasCapability("fw.canbus.unignore"));

// Roadrunner is identified from the plain BusDevice fields, not a
// server-supplied action list - see api/targets.ts's isRoadrunnerDevice for
// why there is no Roadrunner-specific wire field to key off instead.
const canProvisionRoadrunner = computed(() =>
  hasCapability("fw.roadrunner.provision"),
);
const canClearRoadrunner = computed(() => hasCapability("fw.roadrunner.clear"));

/** "unprovisioned" | "provisioned" | null - null both for a non-Roadrunner
 * device and for a Vylyne/Roadrunner serial matching neither known shape
 * (never treated as either state, only as "offer nothing"). */
function roadrunnerRowState(
  device: BusDevice,
): "unprovisioned" | "provisioned" | null {
  if (!isRoadrunnerDevice(device)) return null;
  return roadrunnerIdentityState(device.serial);
}

const canManageTypes = computed(
  () =>
    hasCapability("fw.type.add") &&
    hasCapability("fw.type.update") &&
    hasCapability("fw.type.remove"),
);

const showAdoptItems = computed(
  () => canAdopt.value && mcuTypeNames.value.length > 0,
);
const showCanAdoptItems = computed(
  () => canAdoptCan.value && mcuTypeNames.value.length > 0,
);
const showNewTypeItem = computed(() => canManageTypes.value);
// Whether the `+` trigger itself is worth showing at all - an empty dropdown
// (neither capability present) offers nothing, so the button is omitted
// entirely rather than opening onto a blank menu. Mirrored by
// showCanPlusMenu below for the CAN device rows, which otherwise hid their
// `+` entirely behind "a type already exists to adopt into", gating the
// "New type from this…" entry point that showPlusMenu already offers serial
// devices.
const showPlusMenu = computed(
  () => showAdoptItems.value || showNewTypeItem.value,
);
const showCanPlusMenu = computed(
  () => showCanAdoptItems.value || showNewTypeItem.value,
);

const families = computed(
  () => (state.status?.firmware_families as Family[] | undefined) ?? [],
);

// Keyed by serial - shared across adopt/ignore/unignore so a double click on
// any of a row's async actions can't double-fire, matching the busy-state
// pattern the rest of this app uses per row.
const busy = reactive<Record<string, boolean>>({});

// Only one row's `+` dropdown is open at a time. `useClickOutsideToClose`
// wants one container ref and one open ref (the same pair TargetRow.vue and
// TargetsView.vue use for their own single dropdown); rowRefs holds every
// row's wrapping element so the container ref can be pointed at whichever
// row is currently open.
const rowRefs: Record<string, HTMLElement | null> = {};
function setRowRef(
  serial: string,
  el: Element | ComponentPublicInstance | null,
): void {
  rowRefs[serial] = el as HTMLElement | null;
}
const menuOpenFor = ref<string | null>(null);
const menuContainer = ref<HTMLElement | null>(null);
const menuOpen = computed<boolean>({
  get: () => menuOpenFor.value !== null,
  set: (value) => {
    if (!value) menuOpenFor.value = null;
  },
});
useClickOutsideToClose(menuContainer, menuOpen);
watch(menuOpenFor, (serial) => {
  if (serial !== null)
    void nextTick(() => flipMenuIfOffscreen(rowRefs[serial]));
});

function toggleMenu(serial: string): void {
  if (menuOpenFor.value === serial) {
    menuOpenFor.value = null;
    return;
  }
  menuOpenFor.value = serial;
  menuContainer.value = rowRefs[serial] ?? null;
}

function canKey(device: CanbusDevice): string {
  return `can:${device.uuid}@${device.interface}`;
}

// The `busy[serial]` check up front is a synchronous re-entrancy guard, not
// just UI decoration: `:disabled` only reaches the DOM on Vue's next patch,
// so two clicks landing in the same tick (no await between them, e.g. an
// impatient double-click) would otherwise both pass the disabled check and
// both fire the call.
async function adopt(device: BusDevice, name: string): Promise<void> {
  if (busy[device.serial]) return;
  menuOpenFor.value = null;
  busy[device.serial] = true;
  try {
    await adoptSerial(name, device.serial);
  } finally {
    busy[device.serial] = false;
  }
}

async function adoptCan(device: CanbusDevice, name: string): Promise<void> {
  const key = `can:${device.uuid}`;
  if (busy[key]) return;
  busy[key] = true;
  try {
    await adoptCanbus(name, device.uuid);
  } finally {
    busy[key] = false;
  }
}

async function ignoreCan(device: CanbusDevice): Promise<void> {
  const key = `can:${device.uuid}`;
  if (busy[key]) return;
  busy[key] = true;
  try {
    await ignoreCanbus(device.uuid);
  } finally {
    busy[key] = false;
  }
}

async function unignoreCan(device: CanbusDevice): Promise<void> {
  const key = `can:${device.uuid}`;
  if (busy[key]) return;
  busy[key] = true;
  try {
    await unignoreCanbus(device.uuid);
  } finally {
    busy[key] = false;
  }
}

async function ignore(device: BusDevice): Promise<void> {
  if (busy[device.serial]) return;
  busy[device.serial] = true;
  try {
    await ignoreSerial(device.serial);
  } finally {
    busy[device.serial] = false;
  }
}

async function unignore(device: BusDevice): Promise<void> {
  if (busy[device.serial]) return;
  busy[device.serial] = true;
  try {
    await unignoreSerial(device.serial);
  } finally {
    busy[device.serial] = false;
  }
}

// "Declare a board model with nothing plugged in" was only reachable from the
// toolbar; this is the reverse entry point, for a board already on the bus
// that has no type yet - the chipset it reported gets pre-filled, and it is
// adopted automatically once the type exists (TypeDraft.serial). A CAN
// device carries no chipset (see CanbusScanDevice), so there is nothing to
// suggest there, only a uuid to adopt afterward (TypeDraft.canbusUuid).
const newTypeFor = ref<BusDevice | CanbusDevice | null>(null);

function isCanbusDevice(
  device: BusDevice | CanbusDevice,
): device is CanbusDevice {
  return "uuid" in device;
}

function openNewType(device: BusDevice | CanbusDevice): void {
  newTypeFor.value = device;
  menuOpenFor.value = null;
}

// Clear identity sits behind its own overflow menu rather than as a visible
// row pill, same reasoning TargetRow.vue's own "more actions" menu follows:
// a destructive, infrequent action doesn't need a permanently-visible,
// eye-catching control sitting right next to the device name. Its own key
// (not device.serial) so it can't collide with the "+" track menu, which is
// keyed by serial and may be open on the same row at the same time slot.
function roadrunnerMenuKey(device: BusDevice): string {
  return `rr:${device.serial}`;
}

function openClearConfirm(device: BusDevice): void {
  clearConfirmFor.value = device;
  menuOpenFor.value = null;
}

// Explicit, separate confirmation per action - never combined into one
// dialog, since only one of the two ever applies to a given row's state.
// Neither call tracks the board under a type, so there is nothing here for
// TypeDialog to prefill afterward.
const provisionConfirmFor = ref<BusDevice | null>(null);
const clearConfirmFor = ref<BusDevice | null>(null);

async function confirmProvision(): Promise<void> {
  const device = provisionConfirmFor.value;
  if (!device) return;
  // Synchronous re-entrancy guard, same reasoning as adopt/ignore above:
  // `:disabled` on the Confirm button lands a patch late, too late to stop a
  // second click landing in the same tick.
  if (busy[device.serial]) return;
  busy[device.serial] = true;
  try {
    await provisionRoadrunner(device.serial);
  } finally {
    busy[device.serial] = false;
    provisionConfirmFor.value = null;
  }
}

async function confirmClear(): Promise<void> {
  const device = clearConfirmFor.value;
  if (!device) return;
  if (busy[device.serial]) return;
  busy[device.serial] = true;
  try {
    await clearRoadrunner(device.serial);
  } finally {
    busy[device.serial] = false;
    clearConfirmFor.value = null;
  }
}
</script>

<template>
  <UiPanel
    v-if="
      untracked.length ||
      canbusDevices.length ||
      ignored.length ||
      ignoredCanbus.length ||
      state.canbusError
    "
    title="Untracked devices"
  >
    <p v-if="state.canbusError" class="alert alert--warning">
      CAN scan failed: {{ state.canbusError.message }}. Refresh to try again.
    </p>
    <ul class="devices">
      <li
        v-for="device in untracked"
        :key="device.serial"
        :class="{ muted: device.is_mcu === false }"
      >
        <UiIcon
          :path="device.is_mcu === false ? mdiHelpCircleOutline : mdiUsb"
          size="x-small"
        />
        <span class="device-identity">
          <span class="device-name-row">
            <span class="text--secondary">{{
              roadrunnerDisplaySerial(device.serial)
            }}</span>
            <button
              v-if="
                roadrunnerRowState(device) === 'unprovisioned' &&
                canProvisionRoadrunner
              "
              type="button"
              class="roadrunner-provision"
              :disabled="busy[device.serial]"
              @click="provisionConfirmFor = device"
            >
              Provision Roadrunner
            </button>
            <span
              v-if="
                roadrunnerRowState(device) === 'provisioned' &&
                canClearRoadrunner
              "
              :ref="(el) => setRowRef(roadrunnerMenuKey(device), el)"
              class="target-menu"
            >
              <button
                type="button"
                class="btn-icon btn-icon--small"
                aria-label="Roadrunner actions"
                :disabled="busy[device.serial]"
                @click="toggleMenu(roadrunnerMenuKey(device))"
              >
                <UiIcon :path="mdiDotsVertical" size="x-small" />
              </button>
              <div
                v-if="menuOpenFor === roadrunnerMenuKey(device)"
                class="menu-list"
              >
                <button
                  type="button"
                  class="menu-item menu-item--danger"
                  @click="openClearConfirm(device)"
                >
                  Clear identity
                </button>
              </div>
            </span>
          </span>
          <span class="text--disabled text-caption">{{ device.path }}</span>
        </span>
        <span class="spacer" />

        <span class="text-caption text--disabled">{{ device.fw }}</span>
        <span v-if="device.chipset" class="text-caption text--disabled">{{
          device.chipset
        }}</span>

        <!-- An unprovisioned Roadrunner's serial is
        `RR-UNPROVISIONED-<flash-uid>` - the generic adopt flow would call
        fw.serial.add with that string and persist the RP2040 flash UID into
        printer.cfg, which this plan's constraints forbid (and which goes
        stale the moment the board is actually provisioned). Only the
        "Provision Roadrunner" button above is allowed to touch this row, so
        the track button itself renders disabled with an explanatory tooltip
        rather than being omitted - an omitted button would shift the ignore
        button beside it out of alignment with every other row's icon
        column. Once provisioned, roadrunnerRowState is no longer
        'unprovisioned' and the normal interactive button returns. -->
        <span
          v-if="device.is_mcu !== false && showPlusMenu"
          :ref="(el) => setRowRef(device.serial, el)"
          class="target-menu"
        >
          <button
            v-if="roadrunnerRowState(device) === 'unprovisioned'"
            type="button"
            class="btn-icon btn-icon--small btn-icon--success"
            title="Provision this Roadrunner before it can be tracked"
            disabled
          >
            <UiIcon :path="mdiPlusCircleOutline" size="x-small" />
          </button>
          <template v-else>
            <button
              type="button"
              class="btn-icon btn-icon--small btn-icon--success"
              title="Track this device…"
              :disabled="busy[device.serial]"
              @click="toggleMenu(device.serial)"
            >
              <UiIcon :path="mdiPlusCircleOutline" size="x-small" />
            </button>
            <div v-if="menuOpenFor === device.serial" class="menu-list">
              <template v-if="showAdoptItems">
                <button
                  v-for="name in mcuTypeNames"
                  :key="name"
                  type="button"
                  class="menu-item"
                  :disabled="busy[device.serial]"
                  @click="adopt(device, name)"
                >
                  {{ name }}
                </button>
              </template>
              <hr v-if="showAdoptItems && showNewTypeItem" class="divider" />
              <button
                v-if="showNewTypeItem"
                type="button"
                class="menu-item"
                @click="openNewType(device)"
              >
                New type from this…
              </button>
            </div>
          </template>
        </span>

        <button
          type="button"
          class="btn-icon btn-icon--small btn-icon--warning"
          title="Ignore"
          :disabled="busy[device.serial]"
          @click="ignore(device)"
        >
          <UiIcon :path="mdiCloseCircleOutline" size="x-small" />
        </button>
      </li>
      <li v-for="device in canbusDevices" :key="canKey(device)">
        <UiIcon :path="mdiLan" size="x-small" />
        <span class="device-identity">
          <span class="text--secondary">{{ device.uuid }}</span>
          <span class="text--disabled text-caption">
            CAN {{ device.interface }} · {{ device.state }}
          </span>
        </span>
        <span class="spacer" />
        <span class="device-firmware text-caption text--disabled">{{
          device.application
        }}</span>
        <span
          v-if="showCanPlusMenu"
          :ref="(el) => setRowRef(canKey(device), el)"
          class="target-menu"
        >
          <button
            type="button"
            class="btn-icon btn-icon--small btn-icon--success"
            title="Track this CAN device…"
            :disabled="busy[`can:${device.uuid}`]"
            @click="toggleMenu(canKey(device))"
          >
            <UiIcon :path="mdiPlusCircleOutline" size="x-small" />
          </button>
          <div v-if="menuOpenFor === canKey(device)" class="menu-list">
            <template v-if="showCanAdoptItems">
              <button
                v-for="name in mcuTypeNames"
                :key="name"
                type="button"
                class="menu-item"
                :disabled="busy[`can:${device.uuid}`]"
                @click="adoptCan(device, name)"
              >
                {{ name }}
              </button>
            </template>
            <hr v-if="showCanAdoptItems && showNewTypeItem" class="divider" />
            <button
              v-if="showNewTypeItem"
              type="button"
              class="menu-item"
              @click="openNewType(device)"
            >
              New type from this…
            </button>
          </div>
        </span>
        <button
          v-if="canIgnoreCan"
          type="button"
          class="btn-icon btn-icon--small btn-icon--warning"
          title="Ignore"
          :disabled="busy[`can:${device.uuid}`]"
          @click="ignoreCan(device)"
        >
          <UiIcon :path="mdiCloseCircleOutline" size="x-small" />
        </button>
      </li>
    </ul>

    <details v-if="ignoredCount" class="ignored-devices">
      <summary class="text-caption text--disabled">
        Ignored ({{ ignoredCount }})
      </summary>
      <ul class="devices">
        <li v-for="device in ignored" :key="device.serial">
          <UiIcon
            :path="device.is_mcu === false ? mdiHelpCircleOutline : mdiUsb"
            size="x-small"
          />
          <span class="device-identity">
            <span class="text--secondary">{{
              roadrunnerDisplaySerial(device.serial)
            }}</span>
            <span class="text--disabled text-caption">{{ device.path }}</span>
          </span>
          <span class="spacer" />
          <span class="text-caption text--disabled">{{ device.fw }}</span>
          <span v-if="device.chipset" class="text-caption text--disabled">{{
            device.chipset
          }}</span>
          <button
            type="button"
            class="btn-icon btn-icon--small"
            title="Restore"
            :disabled="busy[device.serial]"
            @click="unignore(device)"
          >
            <UiIcon :path="mdiUndoVariant" size="x-small" />
          </button>
        </li>
        <li v-for="device in ignoredCanbus" :key="canKey(device)">
          <UiIcon :path="mdiLan" size="x-small" />
          <span class="device-identity">
            <span class="text--secondary">{{ device.uuid }}</span>
            <span class="text--disabled text-caption">
              CAN {{ device.interface }} · {{ device.state }}
            </span>
          </span>
          <span class="spacer" />
          <span class="device-firmware text-caption text--disabled">{{
            device.application
          }}</span>
          <button
            v-if="canUnignoreCan"
            type="button"
            class="btn-icon btn-icon--small"
            title="Restore"
            :disabled="busy[`can:${device.uuid}`]"
            @click="unignoreCan(device)"
          >
            <UiIcon :path="mdiUndoVariant" size="x-small" />
          </button>
        </li>
      </ul>
    </details>

    <TypeDialog
      v-if="newTypeFor"
      :existing-names="mcuTypeNames"
      :families="families"
      :suggested-chipset="
        isCanbusDevice(newTypeFor) ? null : newTypeFor.chipset
      "
      :serial="isCanbusDevice(newTypeFor) ? null : newTypeFor.serial"
      :canbus-uuid="isCanbusDevice(newTypeFor) ? newTypeFor.uuid : null"
      @close="newTypeFor = null"
    />

    <UiDialog
      v-if="provisionConfirmFor"
      title="Provision Roadrunner"
      @close="provisionConfirmFor = null"
    >
      <p>
        Provision <strong>{{ provisionConfirmFor.serial }}</strong> (diagnostic
        UID
        <strong>{{
          roadrunnerDiagnosticUid(provisionConfirmFor.serial)
        }}</strong
        >)?
      </p>
      <p class="alert alert--info">
        This is one-shot: the board is given a new random identity that replaces
        the diagnostic UID above, and the write cannot be reverted back to it.
        The board is not tracked by any MCU type afterward - if you want it
        tracked, do that separately once this finishes.
      </p>
      <template #actions>
        <button type="button" @click="provisionConfirmFor = null">
          Cancel
        </button>
        <button
          type="button"
          class="btn-primary"
          :disabled="busy[provisionConfirmFor.serial]"
          @click="confirmProvision"
        >
          Provision
        </button>
      </template>
    </UiDialog>

    <UiDialog
      v-if="clearConfirmFor"
      title="Clear Roadrunner identity"
      @close="clearConfirmFor = null"
    >
      <p>
        Clear the identity of <strong>{{ clearConfirmFor.serial }}</strong
        >?
      </p>
      <p class="alert alert--info">
        This returns the board to its unprovisioned state - it will need to be
        provisioned again before it can be tracked under this identity. The
        board is not tracked by any MCU type either way.
      </p>
      <template #actions>
        <button type="button" @click="clearConfirmFor = null">Cancel</button>
        <button
          type="button"
          class="btn-danger"
          :disabled="busy[clearConfirmFor.serial]"
          @click="confirmClear"
        >
          Clear
        </button>
      </template>
    </UiDialog>
  </UiPanel>
</template>

<style scoped>
/* .menu-list (style.css) is position: absolute against its nearest
   positioned ancestor - without this the dropdown would jump to whatever
   ancestor outside this panel happens to be positioned, rather than sitting
   under its own `+` button. Same rule TargetRow.vue/TargetsView.vue carry for
   their own single dropdown. */
.target-menu {
  position: relative;
}

/* style.css's .device-identity clips overflow so a long name/path never
   pushes the row's trailing content out of view. The Roadrunner "more
   actions" menu now nests inside it (.device-name-row below), and that
   menu is position: absolute - an ancestor's overflow: hidden clips an
   absolutely-positioned descendant too, which would cut the open dropdown
   off at .device-identity's own edge instead of letting it float over the
   row like every other dropdown in this app. overflow: visible here
   (scoped to this component only, via Vue's scoped-CSS specificity bump -
   TargetRow.vue's own .device-identity usage is untouched) moves
   truncation onto each line individually instead, so long text still
   degrades the same way it did before - see .device-name-row
   .text--secondary above and .device-identity > .text-caption below. */
.device-identity {
  overflow: visible;
}

.device-identity > .text-caption {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Sits the "Provision Roadrunner" pill on the same line as the serial name,
   not vertically centered against the whole two-line name+path block the
   way a plain flex sibling of .device-identity would be. min-width: 0 lets
   it shrink inside .device-identity's own cap instead of forcing the row
   wider than its flex basis wants - see .device-name-row .text--secondary
   below for which part actually gives up the space first. */
.device-name-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

/* Optical, not mathematical, centering: align-items: center genuinely
   centers each item's box on the cross axis, but a small pill/icon button
   sitting next to the much larger name text still reads a px or two high -
   text carries more of its visual weight below the box's true centre than
   above it, while a button's padding/border is perfectly symmetric.
   Nudged down rather than switched to align-items: baseline, which would
   align the icon button's synthesized bottom-edge baseline against the
   text's baseline instead - overcorrecting the other way. */
.device-name-row > button,
.device-name-row > .target-menu {
  margin-top: 3.15px;
}

/* The name is what should truncate under pressure, never the pill sitting
   next to it - a clipped "RR-70R656BG…" is still identifiable; a clipped
   "Provision Roadrunn" reads as a bug. flex-shrink: 0 on the pill (below)
   is the other half of this: without both, the flex algorithm shrinks
   whichever child it likes, not necessarily the text. */
.device-name-row .text--secondary {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* .roadrunner-provision otherwise inherits the base `button` dialog-button
   sizing (padding: 6px 14px), which reads as oversized packed between this
   row's 22-28px round icon buttons. Named text, not another icon, is
   deliberate: unlike track/ignore, provisioning is a one-shot identity
   mutation worth spelling out in the row itself, not just behind a
   confirmation dialog - unlike clear (menu-hidden below), it is also the
   *only* thing worth doing to an unprovisioned row, so it stays a
   permanently visible invitation rather than one more click away. Padding
   is trimmed to just enough for the border to clear the text - the pill's
   height should track the font's, not a fixed button height. Outlined, not
   filled: sitting this close to the device name, a solid block would read
   louder than the row's own text. Colour follows the row's existing
   vocabulary rather than a new one - the same green .btn-icon--success
   already uses for "track this device" (both are constructive "claim this
   board" actions). */
.roadrunner-provision {
  padding: 0 8px;
  /* The text's own font metrics carry a bit of built-in top-weighting at
     this line-height, which read as the pill sitting a px or two high next
     to the name even with the row's align-items: center and margin-top
     nudge above. This closes the last of that gap. */
  padding-bottom: 1.15px;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  line-height: 1.15;
  white-space: nowrap;
  background: transparent;
  flex-shrink: 0;
}

.roadrunner-provision:not(:disabled) {
  border-color: var(--tone-ok);
  color: var(--tone-ok);
}

.roadrunner-provision:hover:not(:disabled) {
  background: rgba(76, 175, 80, 0.12);
}

.ignored-devices summary {
  cursor: pointer;
}
</style>
