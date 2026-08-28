<script setup lang="ts">
// fw.status's `bus` - every serial device this host can see, whether or not
// a type claims it. `tracked_by: null` is docs/agent-api.md's "new board,
// want to track it?" case; this is where that gets a UI. See store/agent.ts's
// refreshStatus for why state.bus is not empty on first load.
import { computed, reactive, ref } from "vue";
import { adoptSerial, hasCapability, state } from "../store/agent";
import type { BusDevice, Target } from "../api/targets";
import type { Family } from "../api/mcutype";
import UiPanel from "./UiPanel.vue";
import TypeDialog from "./TypeDialog.vue";

const devices = computed(() => state.bus as unknown as BusDevice[]);
const untracked = computed(() => devices.value.filter((d) => !d.tracked_by));

// Only MCU types can adopt a serial (fw.serial.add) - a display is a
// separate provider with its own port config, not a bus device to claim.
const mcuTypeNames = computed(() => {
  const targets = (state.status?.targets as Target[] | undefined) ?? [];
  return targets
    .filter((t) => t.provider === "kconfig_make")
    .map((t) => t.name);
});

const canAdopt = computed(() => hasCapability("fw.serial.add"));

const canManageTypes = computed(
  () =>
    hasCapability("fw.type.add") &&
    hasCapability("fw.type.update") &&
    hasCapability("fw.type.remove"),
);

const families = computed(
  () => (state.status?.firmware_families as Family[] | undefined) ?? [],
);

const chosen = reactive<Record<string, string>>({});
const adopting = reactive<Record<string, boolean>>({});

async function adopt(device: BusDevice): Promise<void> {
  const name = chosen[device.serial];
  if (!name) return;
  adopting[device.serial] = true;
  await adoptSerial(name, device.serial);
  adopting[device.serial] = false;
}

// "Declare a board model with nothing plugged in" was only reachable from the
// toolbar; this is the reverse entry point, for a board already on the bus
// that has no type yet - the chipset it reported gets pre-filled, and it is
// adopted automatically once the type exists (TypeDraft.serial).
const newTypeFor = ref<BusDevice | null>(null);
</script>

<template>
  <UiPanel v-if="untracked.length" title="Untracked devices">
    <p class="muted">
      On the bus, but no MCU type claims them yet - pick a type to adopt one
      under.
    </p>
    <ul class="devices">
      <li v-for="device in untracked" :key="device.serial">
        <span>{{ device.fw }}</span>
        <span class="muted">{{ device.path }}</span>
        <template v-if="canAdopt && mcuTypeNames.length">
          <select v-model="chosen[device.serial]">
            <option value="" disabled>Adopt as…</option>
            <option v-for="name in mcuTypeNames" :key="name" :value="name">
              {{ name }}
            </option>
          </select>
          <button
            type="button"
            :disabled="!chosen[device.serial] || adopting[device.serial]"
            @click="adopt(device)"
          >
            {{ adopting[device.serial] ? "Adopting…" : "Adopt" }}
          </button>
        </template>
        <button
          v-if="canManageTypes"
          type="button"
          @click="newTypeFor = device"
        >
          New type from this…
        </button>
      </li>
    </ul>

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
