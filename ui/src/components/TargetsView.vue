<script setup lang="ts">
// targets[] said in one shape, rendered through one row component - this is
// Phase 4's whole point. See docs/decisions.md.
import { computed } from "vue";
import TargetRow from "./TargetRow.vue";
import { targetKey, type Target } from "../api/targets";

const props = defineProps<{ targets: Target[] | undefined }>();

const targets = computed(() => props.targets ?? []);
</script>

<template>
  <section>
    <h2>Targets</h2>
    <p v-if="targets.length === 0" class="muted">No targets configured yet.</p>
    <div v-else class="targets">
      <TargetRow
        v-for="target in targets"
        :key="targetKey(target)"
        :target="target"
      />
    </div>
  </section>
</template>
