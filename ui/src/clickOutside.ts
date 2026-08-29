import { onBeforeUnmount, onMounted, type Ref } from "vue";

/** Closes a dropdown menu on an outside click - dialogs deliberately never
 * get this, only menus. A `mousedown` listener (not `click`) so the menu is
 * already closed by the time the outside element's own click runs, the same
 * way a native OS menu behaves. */
export function useClickOutsideToClose(
  container: Ref<HTMLElement | null>,
  open: Ref<boolean>,
): void {
  function onMouseDown(event: MouseEvent): void {
    if (!open.value) return;
    if (container.value?.contains(event.target as Node)) return;
    open.value = false;
  }

  onMounted(() => document.addEventListener("mousedown", onMouseDown));
  onBeforeUnmount(() => document.removeEventListener("mousedown", onMouseDown));
}

/** Flips a just-opened `.menu-list` to grow upward instead of down, when it
 * doesn't fit below its trigger but does fit above - a short panel near the
 * bottom of the page would otherwise grow the page's own scroll height to
 * fit the menu, rather than the menu using the space that's already visible
 * above it. Call after the menu's `v-if` becomes true and Vue has flushed
 * (`await nextTick()`), passing the `.target-menu` container ref the same
 * component already has for `useClickOutsideToClose`. */
export function flipMenuIfOffscreen(container: HTMLElement | null): void {
  const menu = container?.querySelector<HTMLElement>(".menu-list");
  if (!menu) return;
  menu.classList.remove("menu-list--up");
  const rect = menu.getBoundingClientRect();
  const overflowsBelow = rect.bottom > window.innerHeight;
  const fitsAbove = rect.top - rect.height >= 0;
  if (overflowsBelow && fitsAbove) menu.classList.add("menu-list--up");
}
