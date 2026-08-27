<script setup lang="ts">
// One glyph from icons.ts, at one of the sizes the fork panel uses (a plain
// <v-icon> there defaults to 24px; `small`/`x-small` are what the row and
// chip contexts pass explicitly).
const props = withDefaults(
  defineProps<{
    path: string;
    size?: "default" | "small" | "x-small";
  }>(),
  { size: "default" },
);

const PX: Record<string, number> = { default: 24, small: 18, "x-small": 14 };
</script>

<template>
  <svg
    :width="PX[props.size]"
    :height="PX[props.size]"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
  >
    <path :d="props.path" />
  </svg>
</template>
