import pluginVue from "eslint-plugin-vue";
import eslintConfigPrettier from "eslint-config-prettier";
import {
  defineConfigWithVueTs,
  vueTsConfigs,
} from "@vue/eslint-config-typescript";

export default defineConfigWithVueTs(
  pluginVue.configs["flat/recommended"],
  vueTsConfigs.recommended,
  // Must come last: turns off every stylistic rule (eslint-plugin-vue's
  // attribute-wrapping/newline rules included) that Prettier already owns,
  // so eslint --fix and prettier --write stop fighting over the same lines.
  eslintConfigPrettier,
  {
    ignores: ["dist/**", "node_modules/**"],
  },
);
