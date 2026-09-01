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
  hasCapability,
  ignoreCanbus,
  ignoreSerial,
  state,
  unignoreCanbus,
  unignoreSerial,
} from "../store/agent";
import type { BusDevice, Target } from "../api/targets";
import type { CanbusDevice, CanbusScanDevice } from "../store/agent";
import type { Family } from "../api/mcutype";
import UiPanel from "./UiPanel.vue";
import UiIcon from "./UiIcon.vue";
import TypeDialog from "./TypeDialog.vue";
import {
  mdiCloseCircleOutline,
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
// entirely rather than opening onto a blank menu.
const showPlusMenu = computed(
  () => showAdoptItems.value || showNewTypeItem.value,
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
// adopted automatically once the type exists (TypeDraft.serial).
const newTypeFor = ref<BusDevice | null>(null);

function openNewType(device: BusDevice): void {
  newTypeFor.value = device;
  menuOpenFor.value = null;
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
          <span class="text--secondary">{{ device.serial }}</span>
          <span class="text--disabled text-caption">{{ device.path }}</span>
        </span>
        <span class="spacer" />

        <span class="text-caption text--disabled">{{ device.fw }}</span>
        <span v-if="device.chipset" class="text-caption text--disabled">{{
          device.chipset
        }}</span>

        <span
          v-if="device.is_mcu !== false && showPlusMenu"
          :ref="(el) => setRowRef(device.serial, el)"
          class="target-menu"
        >
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
          v-if="showCanAdoptItems"
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
            <span class="text--secondary">{{ device.serial }}</span>
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
      :suggested-chipset="newTypeFor.chipset"
      :serial="newTypeFor.serial"
      @close="newTypeFor = null"
    />
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

.ignored-devices summary {
  cursor: pointer;
}
</style>
