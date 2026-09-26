<script setup lang="ts">
// docs/agent-api.md's "Setting up a brand-new board". Against an agent with
// `fw.add_mcu.scan` and `first_install` on its targets[] rows, every type is
// listed; the row's `first_install` says which flasher sets a bare board of it
// up, or why nothing can, and the scan goes through fw.add_mcu.scan, which
// runs that flasher's own. The wizard never picks a mechanism itself.
//
// Against an older agent - the release order guarantees one, since the UI is
// promoted to stable first - it falls back to exactly the old flow:
// kconfig_make rows, the mechanism read off `descriptor`, fw.dfu.scan /
// fw.bootsel.scan. Remove the fallback once no supported agent lacks
// fw.add_mcu.scan.
//
// Adopting the result (fw.serial.add) and putting the application on a board
// that got a bootloader (fw.flash) are existing, separate flows - JobPanel's
// add_mcu result panel is where the adopt step happens.
import { computed, ref, watch } from "vue";
import {
  firstInstallAware,
  scanBareBoard,
  scanNewBoard,
  startAddMcu,
  state,
} from "../store/agent";
import type { Target } from "../api/targets";
import UiDialog from "./UiDialog.vue";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();
const scanning = ref(false);
const starting = ref(false);
const scan = ref<Record<string, unknown> | null>(null);
const chosenName = ref("");
const chosenDfuSerial = ref("");

const allTargets = computed(
  () => (state.status?.targets as Target[] | undefined) ?? [],
);
const aware = computed(() => firstInstallAware(allTargets.value));
const listed = computed(() =>
  aware.value
    ? allTargets.value
    : allTargets.value.filter((t) => t.provider === "kconfig_make"),
);
const chosen = computed(() =>
  listed.value.find((t) => t.name === chosenName.value),
);

// The flasher that finds and writes a bare board of the chosen type. On the
// legacy path, the two the old flow knew, read off the chipset descriptor.
const flasher = computed<string | null>(() => {
  const target = chosen.value;
  if (!target) return null;
  if (aware.value) return target.first_install?.flasher ?? null;
  if (target.descriptor.startsWith("rp2040")) return "bootsel";
  if (target.descriptor.startsWith("stm32")) return "dfu_util";
  return null;
});

const whyNot = computed<string | null>(() => {
  if (!chosen.value || flasher.value) return null;
  if (aware.value)
    return (
      chosen.value.first_install?.reason ??
      "This type cannot be set up from a bare board."
    );
  return (
    `${chosenName.value}'s chipset has no DFU/BOOTSEL setup path - only ` +
    "STM32 (DFU) and RP2040 (BOOTSEL) boards can be added this way."
  );
});

// What goes on the board, when the agent says; the old agent does not.
const installLabel = computed(() => {
  const fw = aware.value ? chosen.value?.first_install?.fw : null;
  return fw ? `Install ${fw}` : "Install firmware";
});

const ready = computed(() => scan.value?.ready === true);
const reason = computed(() => scan.value?.reason as string | null | undefined);
const message = computed(
  () => scan.value?.message as string | null | undefined,
);
const scanDevices = computed(
  () => (scan.value?.devices as Record<string, unknown>[] | undefined) ?? [],
);
// DFU is the one mechanism that can target one of several boards
// (`dfu_serial`), so only it gets a pick - a property of the mechanism,
// documented in the spec, not a caller branch.
const canPick = computed(
  () => flasher.value === "dfu_util" && reason.value === "ambiguous",
);

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    scan.value = null;
    chosenName.value = "";
    chosenDfuSerial.value = "";
  },
);

function close(): void {
  emit("close");
}

async function runScan(): Promise<void> {
  if (!flasher.value) return;
  scanning.value = true;
  scan.value = aware.value
    ? await scanNewBoard(chosenName.value)
    : await scanBareBoard(flasher.value === "dfu_util" ? "dfu" : "bootsel");
  scanning.value = false;
}

async function start(): Promise<void> {
  starting.value = true;
  const ok = await startAddMcu(
    chosenName.value,
    canPick.value ? chosenDfuSerial.value : undefined,
  );
  starting.value = false;
  if (ok) close();
}
</script>

<template>
  <UiDialog v-if="props.open" title="Add a new board" @close="close">
    <label>
      Type
      <select v-model="chosenName" @change="scan = null">
        <option value="" disabled>Choose a type…</option>
        <option
          v-for="target in listed"
          :key="target.name"
          :value="target.name"
        >
          {{ target.name }} ({{ target.descriptor }})
        </option>
      </select>
    </label>

    <p v-if="whyNot" class="muted">{{ whyNot }}</p>

    <template v-if="flasher">
      <p class="muted">
        Put the board in its boot ROM - fit the boot jumper, or hold BOOT /
        BOOTSEL - and plug it in, then scan.
        <template v-if="chosen?.first_install?.fw">
          This writes {{ chosen.first_install.fw }}.
        </template>
      </p>
      <button type="button" :disabled="scanning" @click="runScan">
        {{ scanning ? "Scanning…" : "Scan" }}
      </button>

      <div v-if="scan">
        <p v-if="ready">Ready - one board found.</p>
        <p v-else-if="message" class="muted">{{ message }}</p>

        <template v-if="canPick">
          <p class="muted">
            More than one board in DFU. Pick the one at the port you mean to
            flash - the path is the only field that says which one.
          </p>
          <select v-model="chosenDfuSerial">
            <option value="" disabled>Choose a device…</option>
            <option
              v-for="device in scanDevices"
              :key="String(device.serial)"
              :value="device.serial"
            >
              {{ device.path }} ({{ device.serial }})
            </option>
          </select>
        </template>

        <button
          type="button"
          :disabled="starting || !(ready || (canPick && chosenDfuSerial))"
          @click="start"
        >
          {{ starting ? "Starting…" : installLabel }}
        </button>
      </div>
    </template>
  </UiDialog>
</template>
