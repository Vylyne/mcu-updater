<script setup lang="ts">
// docs/agent-api.md's "Setting up a brand-new board": fw.dfu.scan/
// fw.bootsel.scan to see what's there, then fw.add_mcu.start to write
// Katapult. Adopting the result (fw.serial.add) and putting Klipper on it
// (fw.flash) are existing, separate flows - JobPanel's add_mcu result panel
// is where the adopt step actually happens, once the job succeeds.
import { computed, ref } from "vue";
import {
  hasCapability,
  scanBareBoard,
  startAddMcu,
  state,
} from "../store/agent";
import type { Target } from "../api/targets";
import UiIcon from "./UiIcon.vue";
import UiDialog from "./UiDialog.vue";
import { mdiDeveloperBoard } from "../icons";

const props = withDefaults(
  defineProps<{
    /** "icon" (default) is the standalone launcher this used to always be.
     * "menu" renders as a row inside TargetsView's own `⋮` menu instead,
     * alongside "New type…" - same dialog, same state, just a different
     * trigger element so the two launchers can share one menu. */
    variant?: "icon" | "menu";
    /** Keep mounted while the menu launcher is hidden. */
    launcherVisible?: boolean;
  }>(),
  { variant: "icon", launcherVisible: true },
);

const open = ref(false);
const scanning = ref(false);
const starting = ref(false);
const scan = ref<Record<string, unknown> | null>(null);
const chosenName = ref("");
const chosenDfuSerial = ref("");

const mcuTargets = computed(() =>
  ((state.status?.targets as Target[] | undefined) ?? []).filter(
    (t) => t.provider === "kconfig_make",
  ),
);

const canStart = computed(() => hasCapability("fw.add_mcu.start"));

const mechanism = computed<"dfu" | "bootsel" | null>(() => {
  const target = mcuTargets.value.find((t) => t.name === chosenName.value);
  if (!target) return null;
  if (target.descriptor.startsWith("rp2040")) return "bootsel";
  if (target.descriptor.startsWith("stm32")) return "dfu";
  return null;
});

const ready = computed(() => scan.value?.ready === true);
const reason = computed(() => scan.value?.reason as string | null | undefined);
const message = computed(
  () => scan.value?.message as string | null | undefined,
);
const scanDevices = computed(
  () => (scan.value?.devices as Record<string, unknown>[] | undefined) ?? [],
);

function show(): void {
  open.value = true;
  scan.value = null;
  chosenName.value = "";
  chosenDfuSerial.value = "";
}

function close(): void {
  open.value = false;
}

async function runScan(): Promise<void> {
  if (!mechanism.value) return;
  scanning.value = true;
  scan.value = await scanBareBoard(mechanism.value);
  scanning.value = false;
}

async function start(): Promise<void> {
  starting.value = true;
  const ok = await startAddMcu(
    chosenName.value,
    mechanism.value === "dfu" && reason.value === "ambiguous"
      ? chosenDfuSerial.value
      : undefined,
  );
  starting.value = false;
  if (ok) close();
}
</script>

<template>
  <span class="add-mcu-wizard">
    <button
      v-if="canStart && mcuTargets.length && props.variant === 'icon'"
      type="button"
      class="btn-icon"
      title="Add new board…"
      @click="show"
    >
      <UiIcon :path="mdiDeveloperBoard" size="small" />
    </button>
    <button
      v-if="
        canStart &&
        mcuTargets.length &&
        props.variant === 'menu' &&
        props.launcherVisible
      "
      type="button"
      class="menu-item"
      @click="show"
    >
      <UiIcon :path="mdiDeveloperBoard" size="x-small" />
      Add new board…
    </button>

    <UiDialog v-if="open" title="Add a new board" @close="close">
      <label>
        Type
        <select v-model="chosenName" @change="scan = null">
          <option value="" disabled>Choose a type…</option>
          <option
            v-for="target in mcuTargets"
            :key="target.name"
            :value="target.name"
          >
            {{ target.name }} ({{ target.descriptor }})
          </option>
        </select>
      </label>

      <p v-if="chosenName && !mechanism" class="muted">
        {{ chosenName }}'s chipset has no DFU/BOOTSEL setup path - only STM32
        (DFU) and RP2040 (BOOTSEL) boards can be added this way.
      </p>

      <template v-if="mechanism">
        <p class="muted">
          {{ mechanism === "dfu" ? "DFU (STM32)" : "BOOTSEL (RP2040)" }} - fit
          the boot jumper (or hold BOOTSEL) and plug the board in, then scan.
        </p>
        <button type="button" :disabled="scanning" @click="runScan">
          {{ scanning ? "Scanning…" : "Scan" }}
        </button>

        <div v-if="scan">
          <p v-if="ready">Ready - one board found.</p>
          <p v-else-if="message" class="muted">{{ message }}</p>

          <template v-if="mechanism === 'dfu' && reason === 'ambiguous'">
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
            :disabled="
              starting ||
              !(
                ready ||
                (reason === 'ambiguous' &&
                  mechanism === 'dfu' &&
                  chosenDfuSerial)
              )
            "
            @click="start"
          >
            {{ starting ? "Starting…" : "Install Katapult" }}
          </button>
        </div>
      </template>
    </UiDialog>
  </span>
</template>

<style scoped>
/* Only needed for variant="menu": the span wrapper otherwise stays inline
   and shrinks to fit, which would leave its menu-item button narrower than
   the other rows in the same .menu-list column. */
.add-mcu-wizard {
  display: block;
}
</style>
