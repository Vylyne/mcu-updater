#!/bin/bash
# Installs the mcu-updater Moonraker agent.
#
# Idempotent: safe to re-run, which matters because Moonraker's update manager
# re-runs it after every update.

KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
KATAPULT_PATH="${KATAPULT_PATH:-${HOME}/katapult}"
PRINTER_DATA="${PRINTER_DATA:-${HOME}/printer_data}"
INSTALL_PATH="${INSTALL_PATH:-${HOME}/mcu-updater}"
CONFIG_PATH="${CONFIG_PATH:-${PRINTER_DATA}/config/mcu-updater}"
DATA_PATH="${DATA_PATH:-${PRINTER_DATA}/mcu-updater}"
# Where the standalone UI's installed build lives - deliberately a sibling of
# INSTALL_PATH, never inside it or inside PRINTER_DATA/mcu-updater. Moonraker's
# `type: web` update manager refuses a path inside a git repo (INSTALL_PATH is
# one), and it rmtree()s this path on every update - one level up from
# DATA_PATH is .updater.state, the flash-recovery journal, which must never
# share a directory with something that gets wiped on a schedule. See
# docs/decisions.md. This default also matches ~/mainsail and ~/fluidd.
UI_PATH="${UI_PATH:-${HOME}/mcu-updater-ui}"
# nginx site for the standalone UI. Port chosen clear of 80/81
# (mainsail/fluidd) and 8080-8083 (the four mjpgstreamer upstreams KIAUH wires
# up by default).
MCU_UPDATER_UI_PORT="${MCU_UPDATER_UI_PORT:-8090}"
MCU_UPDATER_UI_SERVER_NAME="${MCU_UPDATER_UI_SERVER_NAME:-_}"
# Which Moonraker `channel:` this install tracks. `stable` (the default)
# refuses to seed a prerelease build during a beta-only window rather than
# silently crossing channels - see install_ui_release. Set to `beta` to track
# betas cut from the `develop` branch (see AGENTS.md's release sequence).
MCU_UPDATER_UI_CHANNEL="${MCU_UPDATER_UI_CHANNEL:-stable}"
case "${MCU_UPDATER_UI_CHANNEL}" in
    stable | beta) ;;
    *)
        echo "MCU_UPDATER_UI_CHANNEL must be 'stable' or 'beta', got '${MCU_UPDATER_UI_CHANNEL}'" >&2
        exit 1
        ;;
esac
# One file for everything hand-edited: the [updater] section and the [mcu ...]
# sections. Must match Paths.main_config.
MAIN_CONFIG="${CONFIG_PATH}/mcu-updater.cfg"
# udev rule letting dfu-util open a bare STM32 without root.
DFU_UDEV_RULE="${DFU_UDEV_RULE:-/etc/udev/rules.d/99-mcu-updater-dfu.rules}"
# udev rule mounting an RP2040's BOOTSEL mass-storage volume without root.
BOOTSEL_UDEV_RULE="${BOOTSEL_UDEV_RULE:-/etc/udev/rules.d/99-mcu-updater-bootsel.rules}"
# tmpfiles.d entry pre-creating the BOOTSEL mountpoint parents as ${USER}.
BOOTSEL_TMPFILES_CONF="${BOOTSEL_TMPFILES_CONF:-/etc/tmpfiles.d/mcu-updater-bootsel.conf}"
# Constrained from two directions:
#  * Moonraker only permits a `managed_services` value equal to the
#    [update_manager <name>] section, `klipper`, or `moonraker` - so the unit name
#    and that section name must agree.
#  * KIAUH finds instances with ^<component>(-[0-9a-zA-Z]+)?\.service$, so a unit
#    called klipper-* is taken for a Klipper instance and crashes its menu.
# Hence a name that starts with no component name at all.
SERVICE_NAME="mcu-updater"
LEGACY_SERVICE_NAMES="klipper_updater_agent klipper-updater"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

set -eu
export LC_ALL=C

function preflight_checks {
    if [ "$EUID" -eq 0 ]; then
        echo "[PRE-CHECK] This script must not be run as root!"
        exit 1
    fi

    if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F 'moonraker.service')" ]; then
        printf "[PRE-CHECK] Moonraker service found! Continuing...\n\n"
    else
        echo "[ERROR] Moonraker service not found. This agent is useless without it."
        exit 1
    fi

    if [ -z "${PYTHON_BIN}" ]; then
        echo "[ERROR] python3 not found on PATH."
        exit 1
    fi

    # 3.11 is the floor: Raspberry Pi OS Bookworm ships it, and Trixie ships
    # 3.13. Note this is the *system* python3 by design - the agent runs under
    # it (see the unit's %PYTHON%), and katapult's flashtool.py is invoked with
    # sys.executable and needs apt's python3-serial, which a plain venv cannot
    # see. Do not "fix" this by pointing PYTHON_BIN at one.
    if ! "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        echo "[ERROR] python3 >= 3.11 required, found $(${PYTHON_BIN} -V)"
        exit 1
    fi
    printf "[PRE-CHECK] Using %s (%s)\n\n" "${PYTHON_BIN}" "$(${PYTHON_BIN} -V)"
}

function check_paths {
    # Warnings, not errors: the agent is still worth having for status alone, and
    # the individual capabilities degrade rather than the whole thing failing.
    if [ ! -d "${KLIPPER_PATH}" ]; then
        echo "[WARN] ${KLIPPER_PATH} not found - klipper firmware cannot be built."
    fi
    if [ ! -f "${KATAPULT_PATH}/scripts/flashtool.py" ]; then
        echo "[WARN] ${KATAPULT_PATH}/scripts/flashtool.py not found - flashing unavailable."
    fi
    if [ ! -f "${KLIPPER_PATH}/lib/kconfiglib/kconfiglib.py" ]; then
        echo "[WARN] vendored kconfiglib not found - the web config editor will be unavailable."
    fi
    if [ ! -S "${PRINTER_DATA}/comms/moonraker.sock" ]; then
        echo "[WARN] ${PRINTER_DATA}/comms/moonraker.sock not present yet."
        echo "       The agent retries on a loop, so this resolves itself once Moonraker is up."
    fi
    printf "\n"
}

function check_flash_deps {
    # flashtool.py does `import serial`. Without pyserial the failure lands in the
    # middle of a flash, with klipper already stopped - so check it up front and
    # offer to fix it.
    if "${PYTHON_BIN}" -c 'import serial' >/dev/null 2>&1; then
        printf "[DEPS] pyserial present.\n\n"
        return 0
    fi

    echo "[DEPS] python3-serial is missing. katapult's flashtool.py needs it, and"
    echo "       without it a flash fails part-way with klipper already stopped."
    local answer=""
    read -r -p "[DEPS] Install python3-serial with apt now? [Y/n]: " answer || answer=""
    case "${answer}" in
        n | N | no | NO)
            echo "[WARN] Skipped. Flashing will not work until you run:"
            printf "       sudo apt install python3-serial\n\n"
            return 0
            ;;
    esac

    if sudo apt-get install -y python3-serial; then
        if "${PYTHON_BIN}" -c 'import serial' >/dev/null 2>&1; then
            printf "[DEPS] Installed.\n\n"
        else
            # e.g. PYTHON_BIN is a venv without system site-packages.
            echo "[WARN] python3-serial installed, but ${PYTHON_BIN} still cannot import it."
            printf "       Flashing will not work until that interpreter can.\n\n"
        fi
    else
        printf "[WARN] apt install failed. Run 'sudo apt install python3-serial' yourself.\n\n"
    fi
}

function check_dfu_permissions {
    # dfu-util needs raw USB access. Without a udev rule it prints
    #   Cannot open DFU device 0483:df11 ... (LIBUSB_ERROR_ACCESS)
    # and lists nothing - which for a long time we reported as "no DFU device
    # detected, hold BOOT0 and replug", sending people to redo the one step that
    # had actually worked. A rule fixes it for both the CLI and the agent, so
    # neither needs sudo at flash time.
    #
    # Only relevant for installing Katapult onto a bare STM32 (add-mcu). Boards
    # that already have Katapult never touch dfu-util.
    if ! command -v dfu-util >/dev/null 2>&1; then
        printf "[DFU]  dfu-util not installed - only needed for add-mcu on a bare board.\n\n"
        return 0
    fi
    if [ -f "${DFU_UDEV_RULE}" ]; then
        printf "[DFU]  udev rule already present.\n\n"
        return 0
    fi

    echo "[DFU]  No udev rule for STM32 DFU mode (${DFU_UDEV_RULE})."
    echo "       Without it dfu-util cannot open a board in DFU mode, and add-mcu"
    echo "       fails on a board whose boot jumper is perfectly fine."
    local answer=""
    read -r -p "[DFU]  Install the udev rule now? [Y/n]: " answer || answer=""
    case "${answer}" in
        n | N | no | NO)
            echo "[WARN] Skipped. add-mcu on a bare board will need sudo until you add it."
            printf "\n"
            return 0
            ;;
    esac

    local tmp
    tmp="$(mktemp)"
    cat > "${tmp}" <<'RULE'
# STM32 in DFU mode (0483:df11) - lets dfu-util open the device without root, so
# mcu-updater's add-mcu works from the CLI and from the Moonraker agent.
# Installed by mcu-updater's install.sh.
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="df11", MODE="0664", GROUP="plugdev", TAG+="uaccess"
RULE
    sudo install -m 0644 -o root -g root "${tmp}" "${DFU_UDEV_RULE}"
    rm -f "${tmp}"

    # The rule grants access to the plugdev group, so the service account has to
    # be in it. A group change needs a fresh login - or for a daemon, a restart,
    # which install_service does later anyway.
    if getent group plugdev >/dev/null 2>&1; then
        if ! id -nG "${USER}" | tr ' ' '\n' | grep -qx plugdev; then
            echo "[DFU]  Adding ${USER} to the plugdev group..."
            sudo usermod -aG plugdev "${USER}"
            echo "[DFU]  Log out and back in for your shell to pick that up."
        fi
    else
        echo "[WARN] No plugdev group on this system; relying on TAG+=\"uaccess\"."
    fi

    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb
    echo "[DFU]  Rule installed. Replug a board in DFU mode for it to take effect."
    printf "\n"
}

# The BOOTSEL mountpoint parents, pre-created with known ownership and mode
# instead of the root-owned ones systemd-mount creates implicitly. That is a
# nicety, not what makes the mount work - so every failure in here warns and
# returns 0. install.sh re-runs unattended after every Moonraker update, and
# under `set -e` a nonzero exit here would abort the whole installer before
# check_config, install_service and restart_moonraker, leaving a half-applied
# update over a directory-ownership hint. Sets BOOTSEL_TMPFILES_DONE to 1 only
# if the entry really landed, so no caller claims it did when it did not.
function install_bootsel_tmpfiles {
    BOOTSEL_TMPFILES_DONE=0
    if [ -z "${USER:-}" ]; then
        echo "[WARN] USER is empty; skipping ${BOOTSEL_TMPFILES_CONF}."
        echo "       Substituting nothing would declare /media/BOOTSEL owned by no one."
        return 0
    fi
    local tmp
    tmp="$(mktemp)"
    # Not `sed ... > tmp` bare: an INSTALL_PATH that does not resolve, or a
    # renamed template, would exit nonzero and take the installer with it - and
    # the self-heal caller reaches here with no prompt in front of it.
    if ! sed -e "s|%USER%|${USER}|g" \
        "${INSTALL_PATH}/scripts/tmpfiles.d-mcu-updater-bootsel.conf" > "${tmp}" 2>/dev/null; then
        rm -f "${tmp}"
        echo "[WARN] Could not read"
        echo "       ${INSTALL_PATH}/scripts/tmpfiles.d-mcu-updater-bootsel.conf;"
        echo "       skipping ${BOOTSEL_TMPFILES_CONF}. BOOTSEL flashing still works."
        return 0
    fi
    # tmpfiles.d reads % as a specifier prefix (%U is the UID), so a %USER% that
    # outlived the sed - a USER containing sed's & is how that happens - would
    # name a directory nobody meant to create. Belt and braces after the -n test.
    if grep -q '%USER%' "${tmp}"; then
        rm -f "${tmp}"
        echo "[WARN] %USER% survived substitution; skipping ${BOOTSEL_TMPFILES_CONF}."
        echo "       systemd-mount creates the mountpoint parents either way, so"
        echo "       BOOTSEL flashing still works - they will just be root-owned."
        return 0
    fi
    if ! sudo install -m 0644 -o root -g root "${tmp}" "${BOOTSEL_TMPFILES_CONF}"; then
        rm -f "${tmp}"
        echo "[WARN] Could not install ${BOOTSEL_TMPFILES_CONF}; continuing."
        return 0
    fi
    rm -f "${tmp}"
    if ! sudo systemd-tmpfiles --create "${BOOTSEL_TMPFILES_CONF}"; then
        echo "[WARN] systemd-tmpfiles --create ${BOOTSEL_TMPFILES_CONF} failed."
        echo "       The entry is installed and will be applied at next boot; until"
        echo "       then systemd-mount creates the parents itself, root-owned."
    fi
    BOOTSEL_TMPFILES_DONE=1
    return 0
}

function check_bootsel_permissions {
    # An RP2040 in BOOTSEL mounts as USB mass storage. A headless printer has no
    # desktop automounter, so nothing mounts it and bootsel_scan finds nothing -
    # which we used to report as "give it a moment to automount", sending people
    # to redo the one step that had actually worked. A rule fixes it for both
    # the CLI and the agent, so neither needs sudo at flash time.
    #
    # Only relevant for writing the first bootloader onto a bare RP2040
    # (add-mcu). Boards that already have Katapult never touch this path.
    if ! command -v systemd-mount >/dev/null 2>&1; then
        printf "[BOOTSEL]  systemd-mount not found - only needed for add-mcu on a bare RP2040.\n\n"
        return 0
    fi
    # Both files installed here are templated on ${USER}. An empty one does not
    # fail loudly: it would mount at /media//BOOTSEL and hand the volume to no
    # one, so refuse the whole thing rather than install a rule that misfires.
    if [ -z "${USER:-}" ]; then
        echo "[WARN] USER is empty; skipping the BOOTSEL udev rule and tmpfiles.d entry."
        echo "       Both are templated on it - see docs/bootsel-mountpoint-design.md."
        printf "\n"
        return 0
    fi
    # A version check, not a presence check. The first rule mounted every board
    # at one fixed path; a host that already had it would otherwise never
    # receive the topology-path rule that fixes two-boards-at-once - which is
    # exactly backwards, since those are the hosts using this feature.
    local shipped installed=0 have_rule=0
    shipped="$(grep -m1 -o 'mcu-updater-bootsel-rule-version: [0-9]\+' \
        "${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules" 2>/dev/null \
        | grep -o '[0-9]\+' || true)"
    shipped="${shipped:-0}"
    if [ "${shipped}" -eq 0 ]; then
        # Failing open here degrades to exactly the bug this check exists to fix:
        # every installed rule compares as up to date, so a host on the old
        # fixed-path rule silently keeps it. Say so instead of returning quietly.
        echo "[WARN] Could not read a version marker from"
        echo "       ${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules."
        echo "       Any already-installed rule will therefore look current, so a host"
        echo "       still on the old fixed-path rule would not be upgraded. Check the"
        echo "       file exists and carries its mcu-updater-bootsel-rule-version line."
    fi

    if [ -f "${BOOTSEL_UDEV_RULE}" ]; then
        have_rule=1
        installed="$(grep -m1 -o 'mcu-updater-bootsel-rule-version: [0-9]\+' \
            "${BOOTSEL_UDEV_RULE}" 2>/dev/null | grep -o '[0-9]\+' || true)"
        installed="${installed:-0}"
        if [ "${installed}" -ge "${shipped}" ]; then
            printf "[BOOTSEL]  udev rule already present (version %s).\n" "${installed}"
            # Self-heal only where consent already exists. A rule on disk means
            # this prompt was answered yes at some point; anyone who installed
            # between the rule's version bump and the tmpfiles.d entry being
            # wired in is current with no conf, and the version check alone
            # would never bring them one. No rule on disk means no consent, so
            # this never runs for someone who declined.
            if [ ! -f "${BOOTSEL_TMPFILES_CONF}" ]; then
                echo "[BOOTSEL]  Mountpoint parents are not declared yet - installing"
                echo "           ${BOOTSEL_TMPFILES_CONF}."
                install_bootsel_tmpfiles
            fi
            printf "\n"
            return 0
        fi
        echo "[BOOTSEL]  Installed udev rule is version ${installed}, shipped is ${shipped}."
        echo "           The old rule mounts every RP2040 at one fixed path, so two boards"
        echo "           in BOOTSEL collide and one spare board blocks flashing the other."
    else
        echo "[BOOTSEL]  No udev rule to mount an RP2040's BOOTSEL volume (${BOOTSEL_UDEV_RULE})."
        echo "           Without it nothing mounts the volume on a headless printer, and"
        echo "           add-mcu fails on a board whose BOOTSEL mode is perfectly fine."
    fi
    echo "           Installing writes ${BOOTSEL_UDEV_RULE}"
    echo "           and ${BOOTSEL_TMPFILES_CONF} as root, then runs"
    echo "           udevadm and systemd-tmpfiles --create."
    local answer=""
    read -r -p "[BOOTSEL]  Install the udev rule and its tmpfiles.d entry now? [Y/n]: " answer || answer=""
    case "${answer}" in
        n | N | no | NO)
            if [ "${have_rule}" -eq 1 ]; then
                echo "[WARN] Skipped. Keeping the version ${installed} rule, which still mounts"
                echo "       one board fine - but two RP2040s in BOOTSEL at once still collide"
                echo "       on its single mountpoint, and one spare blocks flashing the other."
            else
                echo "[WARN] Skipped. add-mcu on a bare RP2040 will need a manual mount until you add it."
            fi
            printf "\n"
            return 0
            ;;
    esac

    local tmp
    tmp="$(mktemp)"
    # Not `sed ... > tmp` bare: the "could not read the template" warning above
    # is reachable (shipped=0), and this would otherwise abort the installer
    # right after it, taking install_service and restart_moonraker down with it.
    if ! sed -e "s|%USER%|${USER}|g" \
        "${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules" > "${tmp}" 2>/dev/null; then
        rm -f "${tmp}"
        echo "[WARN] Could not read"
        echo "       ${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules;"
        echo "       skipping the BOOTSEL udev rule. BOOTSEL flashing will need a manual mount."
        return 0
    fi
    sudo install -m 0644 -o root -g root "${tmp}" "${BOOTSEL_UDEV_RULE}"
    rm -f "${tmp}"

    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=block

    install_bootsel_tmpfiles
    if [ "${BOOTSEL_TMPFILES_DONE:-0}" -eq 1 ]; then
        echo "[BOOTSEL]  Rule and tmpfiles.d entry installed. Replug a board in BOOTSEL"
        echo "           mode for it to take effect."
    else
        echo "[BOOTSEL]  Rule installed; the tmpfiles.d entry was skipped for the reason"
        echo "           above. Replug a board in BOOTSEL mode for the rule to take"
        echo "           effect - the mountpoint parents will just be root-owned."
    fi
    printf "\n"
}

function check_config {
    mkdir -p "${CONFIG_PATH}" "${DATA_PATH}"

    # A registry left at the pre-0.10 location would otherwise read as "no MCU
    # types configured", and the next add-type would write a fresh file while the
    # real one sat untouched. Refuse loudly instead.
    if [ -f "${HOME}/mcus/mcus.json" ] && [ ! -f "${MAIN_CONFIG}" ]; then
        echo "[ERROR] Found an old registry at ${HOME}/mcus/mcus.json but nothing at"
        echo "        ${MAIN_CONFIG}."
        echo "        The layout moved - see docs/layout.md for the handful of commands."
        echo "        Refusing to continue so an empty registry cannot overwrite anything."
        exit 1
    fi

    # Same again for the previous split-file layout. Settings now live in the
    # [updater] section of the main config; a leftover updater.conf is no longer
    # read, and enable_flashing silently reverting to false is worth saying out
    # loud rather than leaving someone to wonder where the flash buttons went.
    if [ -f "${CONFIG_PATH}/updater.conf" ]; then
        echo "[WARN]  ${CONFIG_PATH}/updater.conf is no longer read."
        echo "        Settings moved into the [updater] section of ${MAIN_CONFIG}."
        echo "        Copy anything you had set across, then delete it."
        printf "\n"
    fi
    if [ -f "${CONFIG_PATH}/mcus.cfg" ] && [ ! -f "${MAIN_CONFIG}" ]; then
        echo "[ERROR] The registry is now ${MAIN_CONFIG}, not ${CONFIG_PATH}/mcus.cfg."
        echo "        Rename it (settings and the [mcu ...] sections share one file now):"
        echo "            mv ${CONFIG_PATH}/mcus.cfg ${MAIN_CONFIG}"
        exit 1
    fi

    # A broken registry is surfaced here, loudly, rather than by the agent
    # reporting it as an error to the UI after the fact.
    if [ ! -f "${MAIN_CONFIG}" ]; then
        printf "[CONFIG] No config at %s yet - nothing to validate.\n\n" "${MAIN_CONFIG}"
        return 0
    fi
    # A traceback here would be noise: the exception message already says exactly
    # what is wrong and how to fix it, so print that and nothing else.
    if PYTHONPATH="${INSTALL_PATH}/src" "${PYTHON_BIN}" -c '
import sys
from mcu_updater.config import Registry
from mcu_updater.errors import UpdaterError
from mcu_updater.paths import Paths
from mcu_updater.settings import load_settings
paths = Paths.from_env()
try:
    reg = Registry.load(paths)
    print(f"[CONFIG] {len(reg)} MCU type(s), {len(reg.all_serials())} tracked serial(s).")
    # Same file, so a typo in [updater] is worth catching here too - the agent
    # would otherwise fall back to defaults with only a line in its log.
    s = load_settings(paths.settings_file)
    state = "ENABLED" if s.enable_flashing else "disabled"
    print(f"[CONFIG] Flashing from the web UI is {state}.")
except UpdaterError as exc:
    print(f"[ERROR] {exc}", file=sys.stderr)
    sys.exit(1)
'; then
        printf "\n"
    else
        echo "[ERROR] Fix ${MAIN_CONFIG}, then re-run."
        exit 1
    fi
}

function migrate_legacy_service {
    # Two previous names, each removed for a concrete reason:
    #   klipper_updater_agent - Moonraker rejects it as a managed_services value
    #   klipper-updater       - KIAUH mistakes it for a Klipper instance and, if
    #                           the unit is not world-readable, crashes its menu
    # Leaving either behind means two units racing for the same socket.
    local asvc="${PRINTER_DATA}/moonraker.asvc"
    local conf="${PRINTER_DATA}/config/moonraker.conf"
    local backed_up=0
    local legacy_name legacy_unit

    for legacy_name in ${LEGACY_SERVICE_NAMES}; do
        legacy_unit="/etc/systemd/system/${legacy_name}.service"
        if [ -f "${legacy_unit}" ]; then
            echo "[MIGRATE] Removing the old ${legacy_name} service..."
            sudo systemctl stop "${legacy_name}.service" 2>/dev/null || true
            sudo systemctl disable "${legacy_name}.service" 2>/dev/null || true
            sudo rm -f "${legacy_unit}"
            sudo systemctl daemon-reload
        fi

        if [ -f "${asvc}" ] && grep -qxF "${legacy_name}" "${asvc}"; then
            echo "[MIGRATE] Dropping stale ${legacy_name} from moonraker.asvc..."
            sed -i "/^${legacy_name}\$/d" "${asvc}"
        fi

        # Repair a moonraker.conf written by an earlier install. add_update_manager
        # only appends when the section is absent, so without this a re-run would
        # leave the stale section in place.
        if [ -f "${conf}" ] && grep -qE "^\[update_manager ${legacy_name}\]|^managed_services:[[:space:]]*${legacy_name}[[:space:]]*\$" "${conf}"; then
            if [ "${backed_up}" -eq 0 ]; then
                cp "${conf}" "${conf}.bak-mcu-updater"
                backed_up=1
            fi
            echo "[MIGRATE] Renaming the ${legacy_name} update_manager entry..."
            sed -i "s|^\[update_manager ${legacy_name}\]|[update_manager ${SERVICE_NAME}]|" "${conf}"
            sed -i "s|^managed_services:[[:space:]]*${legacy_name}[[:space:]]*\$|managed_services: ${SERVICE_NAME}|" "${conf}"

            # The section also carries path: and origin:, which still point at the
            # old clone and the old repo URL. Renaming only the header leaves
            # Moonraker updating a directory that may not exist any more - and
            # add_update_manager will not fix it, because it only appends when the
            # section is absent. Take the correct values from the shipped template
            # so they are defined in exactly one place.
            local want_path want_origin
            want_path="$(grep -m1 '^path:' "${INSTALL_PATH}/scripts/moonraker-update-manager.conf" || true)"
            want_origin="$(grep -m1 '^origin:' "${INSTALL_PATH}/scripts/moonraker-update-manager.conf" || true)"
            # Plain substitution rather than sed's `c\`, whose backslash handling
            # swallows the expansion and writes the variable name verbatim.
            # Scoped to lines mentioning the old name, so another add-on's
            # update_manager section is never touched. '|' is safe as the
            # delimiter: paths and URLs contain '/', never '|'.
            if [ -n "${want_path}" ]; then
                sed -i "s|^path:.*${legacy_name}.*|${want_path}|" "${conf}"
            fi
            if [ -n "${want_origin}" ]; then
                sed -i "s|^origin:.*${legacy_name}.*|${want_origin}|" "${conf}"
            fi
        fi
    done

    # Whatever happened above, an update_manager path that does not exist means
    # Moonraker will keep erroring on it. Say so rather than leaving it to be
    # discovered later.
    if [ -f "${conf}" ]; then
        local declared
        declared="$(sed -n "/^\[update_manager ${SERVICE_NAME}\]/,/^\[/p" "${conf}" \
            | grep -m1 '^path:' | sed 's/^path:[[:space:]]*//' || true)"
        if [ -n "${declared}" ]; then
            # shellcheck disable=SC2088
            case "${declared}" in "~/"*) declared="${HOME}/${declared#\~/}" ;; esac
            if [ ! -d "${declared}" ]; then
                echo "[WARN] moonraker.conf's update_manager path does not exist: ${declared}"
                echo "       Moonraker will error on that section. Expected ${INSTALL_PATH}."
            fi
        fi
    fi

    if [ "${backed_up}" -eq 1 ]; then
        printf "[MIGRATE] moonraker.conf updated (backup at %s.bak-mcu-updater).\n" "${conf}"
    fi

    # An earlier install.sh used `sudo cp` from a mktemp file, so the unit could
    # be mode 0600 and unreadable to anything scanning /etc/systemd/system.
    local unit="/etc/systemd/system/${SERVICE_NAME}.service"
    if [ -f "${unit}" ] && [ ! -r "${unit}" ]; then
        echo "[MIGRATE] Fixing permissions on ${unit}..."
        sudo chmod 0644 "${unit}"
    fi
    printf "\n"
}

function install_service {
    echo "[INSTALL] Installing systemd unit ${SERVICE_NAME}.service..."
    local tmp
    tmp="$(mktemp)"
    sed -e "s|%USER%|${USER}|g" \
        -e "s|%INSTALL_DIR%|${INSTALL_PATH}|g" \
        -e "s|%PRINTER_DATA%|${PRINTER_DATA}|g" \
        -e "s|%PYTHON%|${PYTHON_BIN}|g" \
        "${INSTALL_PATH}/scripts/${SERVICE_NAME}.service" > "${tmp}"

    # install, not cp: mktemp creates 0600, and cp would carry that mode over,
    # leaving a unit only root can read. Anything that scans /etc/systemd/system
    # as a normal user then dies on it - which is exactly how this broke KIAUH.
    sudo install -m 0644 -o root -g root "${tmp}" "/etc/systemd/system/${SERVICE_NAME}.service"
    rm -f "${tmp}"
    sudo systemctl daemon-reload
    sudo systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
    sudo systemctl restart "${SERVICE_NAME}.service"
    printf "[INSTALL] Service installed and started.\n\n"
}

function add_asvc {
    # Lets you restart the agent from Mainsail's own Services UI.
    local asvc="${PRINTER_DATA}/moonraker.asvc"
    if [ ! -f "${asvc}" ]; then
        echo "[MOONRAKER] ${asvc} not found, skipping allow-list entry."
        return 0
    fi
    if grep -qxF "${SERVICE_NAME}" "${asvc}"; then
        printf "[MOONRAKER] %s already in moonraker.asvc.\n\n" "${SERVICE_NAME}"
    else
        echo "${SERVICE_NAME}" >> "${asvc}"
        printf "[MOONRAKER] Added %s to moonraker.asvc.\n\n" "${SERVICE_NAME}"
    fi
}

function add_update_manager {
    local conf="${PRINTER_DATA}/config/moonraker.conf"
    if [ ! -f "${conf}" ]; then
        echo "[MOONRAKER] ${conf} not found, skipping update_manager entry."
        return 0
    fi
    if grep -q "^\[update_manager ${SERVICE_NAME}\]" "${conf}"; then
        printf "[MOONRAKER] update_manager entry already present.\n\n"
    else
        echo "[MOONRAKER] Adding update_manager entry to moonraker.conf..."
        {
            printf "\n"
            cat "${INSTALL_PATH}/scripts/moonraker-update-manager.conf"
        } >> "${conf}"
        printf "[MOONRAKER] Added. Restart Moonraker for it to take effect.\n\n"
    fi
}

function add_update_manager_ui {
    local conf="${PRINTER_DATA}/config/moonraker.conf"
    if [ ! -f "${conf}" ]; then
        echo "[MOONRAKER] ${conf} not found, skipping mcu-updater-ui update_manager entry."
        return 0
    fi
    if grep -q "^\[update_manager mcu-updater-ui\]" "${conf}"; then
        printf "[MOONRAKER] mcu-updater-ui update_manager entry already present.\n\n"
    else
        echo "[MOONRAKER] Adding mcu-updater-ui update_manager entry to moonraker.conf..."
        {
            printf "\n"
            # The shipped conf hardcodes channel: stable; swap it when this
            # install was asked to track beta instead.
            if [ "${MCU_UPDATER_UI_CHANNEL}" = "beta" ]; then
                sed 's/^channel: stable$/channel: beta/' "${INSTALL_PATH}/scripts/moonraker-update-manager-ui.conf"
            else
                cat "${INSTALL_PATH}/scripts/moonraker-update-manager-ui.conf"
            fi
        } >> "${conf}"
        printf "[MOONRAKER] Added. Restart Moonraker for it to take effect.\n\n"
    fi
}

function install_ui_release {
    # Moonraker will not bootstrap an empty `type: web` directory - no
    # release_info.json means _is_valid=False, and it never downloads (see
    # docs/decisions.md). This performs that first fetch so the update
    # manager has something to compare against from the start.
    if [ -f "${UI_PATH}/release_info.json" ]; then
        printf "[UI] %s already has a release installed.\n\n" "${UI_PATH}"
        return 0
    fi

    if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
        printf "[UI] curl and unzip are required to fetch the UI release - skipping.\n\n"
        return 0
    fi

    echo "[UI] Fetching the latest mcu-updater-ui release (channel: ${MCU_UPDATER_UI_CHANNEL})..."
    # This is only the *bootstrap* fetch - it just needs to land some
    # release_info.json so Moonraker's own update_manager stops refusing to
    # ever check (see the comment above). Every later check follows whatever
    # `channel:` is configured, so this bootstrap must seed the *same*
    # channel and never cross - seeding a prerelease into a channel: stable
    # directory works technically (Moonraker only reads release_info.json),
    # but Mainsail's client-side `semver.gt(remote, local)` then hides every
    # future update row rather than reporting the mismatch. So: stable stays
    # on /releases/latest only, with no fallback to "newest of any kind";
    # beta reads the newest release regardless of its prerelease flag.
    local asset_url=""
    if [ "${MCU_UPDATER_UI_CHANNEL}" = "beta" ]; then
        asset_url="$(curl -fsSL "https://api.github.com/repos/Vylyne/mcu-updater/releases?per_page=1" \
            | grep -o '"browser_download_url": *"[^"]*mcu-updater-ui\.zip"' \
            | grep -o 'https://[^"]*' || true)"
    else
        asset_url="$(curl -fsSL "https://api.github.com/repos/Vylyne/mcu-updater/releases/latest" \
            | grep -o '"browser_download_url": *"[^"]*mcu-updater-ui\.zip"' \
            | grep -o 'https://[^"]*' || true)"
    fi
    if [ -z "${asset_url}" ]; then
        if [ "${MCU_UPDATER_UI_CHANNEL}" = "stable" ]; then
            printf "[UI] No stable release published yet - leaving the placeholder.\n       Re-run install.sh once one exists, or MCU_UPDATER_UI_CHANNEL=beta ./install.sh to track the beta channel instead.\n\n"
        else
            printf "[UI] No release published yet (or the fetch failed) - leaving the placeholder.\n       Re-run install.sh once a release exists.\n\n"
        fi
        return 0
    fi

    mkdir -p "${UI_PATH}"
    local tmp_zip
    tmp_zip="$(mktemp)"
    if ! curl -fsSL "${asset_url}" -o "${tmp_zip}"; then
        echo "[UI] Download failed - leaving the placeholder."
        rm -f "${tmp_zip}"
        return 0
    fi

    # Unzip to a scratch directory first: a partial unzip must never leave
    # UI_PATH half-populated, since nginx may already be serving it.
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    if ! unzip -q -o "${tmp_zip}" -d "${tmp_dir}"; then
        echo "[UI] Unzip failed - leaving the placeholder."
        rm -f "${tmp_zip}"
        rm -rf "${tmp_dir}"
        return 0
    fi
    rm -f "${tmp_zip}"

    find "${UI_PATH}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    cp -r "${tmp_dir}/." "${UI_PATH}/"
    rm -rf "${tmp_dir}"
    printf "[UI] Installed to %s.\n\n" "${UI_PATH}"
}

function allow_sudo_fallback {
    # The normal path needs no sudo: the agent stops klipper through Moonraker's
    # machine.services API. This is purely the safety net for Moonraker dying
    # between the stop and the start, when that API is unreachable and the agent
    # would otherwise be unable to bring klipper back.
    local target="/etc/sudoers.d/mcu-updater"
    if [ -f "${target}" ]; then
        printf "[SUDO] Fallback rule already installed.\n\n"
        return 0
    fi

    cat <<EOF
[SUDO] Optional safety net.

  The agent stops klipper via Moonraker, which needs no special privileges. But
  if Moonraker dies *between* the stop and the start, the agent cannot put
  klipper back, and the printer stays down until you notice.

  Installing a narrow sudoers rule (three exact systemctl commands for the
  klipper unit, no wildcards) lets the agent recover on its own. Declining is
  safe - the systemd unit's ExecStopPost still covers some cases - but the net
  is weaker, and the CLI will prompt for a password when it stops klipper.

EOF
    local answer=""
    read -r -p "[SUDO] Install /etc/sudoers.d/mcu-updater? [y/N]: " answer || answer=""
    case "${answer}" in
        y | Y | yes | YES) ;;
        *)
            printf "[SUDO] Skipped.\n\n"
            return 0
            ;;
    esac

    local tmp
    tmp="$(mktemp)"
    sed -e "s|%USER%|${USER}|g" "${INSTALL_PATH}/scripts/sudoers.d-mcu-updater" > "${tmp}"
    # Validate before installing: a malformed sudoers file can lock you out of
    # sudo entirely, so never copy one in unchecked.
    if sudo visudo -c -f "${tmp}" >/dev/null 2>&1; then
        sudo install -m 0440 -o root -g root "${tmp}" "${target}"
        printf "[SUDO] Installed %s\n\n" "${target}"
    else
        echo "[ERROR] Generated sudoers file failed validation; not installing it."
        sudo visudo -c -f "${tmp}" || true
    fi
    rm -f "${tmp}"
}

function install_nginx_site {
    # Optional: the standalone UI (Phase 2+) is served from here once it
    # exists. Installing the site now, ahead of the UI itself, lets the
    # nginx/proxy layer be verified independently - curl localhost:PORT/server/info
    # should return Moonraker's JSON even with nothing but a placeholder root.
    if ! command -v nginx >/dev/null 2>&1; then
        printf "[NGINX] nginx not installed - skipping the standalone UI site.\n\n"
        return 0
    fi

    local site_dest="/etc/nginx/sites-available/${SERVICE_NAME}"
    local enabled_dest="/etc/nginx/sites-enabled/${SERVICE_NAME}"
    local confd_dest="/etc/nginx/conf.d/${SERVICE_NAME}.conf"

    if [ -f "${site_dest}" ]; then
        printf "[NGINX] Site already installed at %s.\n\n" "${site_dest}"
        return 0
    fi

    cat <<EOF
[NGINX] Optional: a dedicated nginx site for the standalone mcu-updater UI,
        served on its own port alongside Mainsail/Fluidd rather than folded
        into either. Serves from ${UI_PATH} (override with UI_PATH before
        re-running), which the UI's own installer populates.

EOF
    local answer=""
    read -r -p "[NGINX] Install the nginx site now? [y/N]: " answer || answer=""
    case "${answer}" in
        y | Y | yes | YES) ;;
        *)
            printf "[NGINX] Skipped.\n\n"
            return 0
            ;;
    esac

    mkdir -p "${UI_PATH}"
    # A placeholder so the site has something to serve before the standalone
    # UI (Phase 2+) is installed - and so it never overwrites a real build the
    # UI's own installer already dropped here.
    if [ ! -f "${UI_PATH}/index.html" ]; then
        cat > "${UI_PATH}/index.html" <<'HTML'
<!doctype html><html><head><meta charset="utf-8"><title>mcu-updater</title></head>
<body><p>mcu-updater UI not installed yet.</p></body></html>
HTML
    fi

    local tmp_confd
    tmp_confd="$(mktemp)"
    cp "${INSTALL_PATH}/scripts/nginx.conf.d-mcu-updater.conf" "${tmp_confd}"
    sudo install -m 0644 -o root -g root "${tmp_confd}" "${confd_dest}"
    rm -f "${tmp_confd}"

    local tmp_site
    tmp_site="$(mktemp)"
    sed -e "s|%PORT%|${MCU_UPDATER_UI_PORT}|g" \
        -e "s|%SERVER_NAME%|${MCU_UPDATER_UI_SERVER_NAME}|g" \
        -e "s|%ROOT_DIR%|${UI_PATH}|g" \
        -e "s|%NAME%|${SERVICE_NAME}|g" \
        "${INSTALL_PATH}/scripts/nginx.sites-available-mcu-updater" > "${tmp_site}"
    sudo install -m 0644 -o root -g root "${tmp_site}" "${site_dest}"
    rm -f "${tmp_site}"

    sudo ln -sfn "${site_dest}" "${enabled_dest}"

    # Never reload on a config nginx itself rejects - that would take down every
    # other site (Mainsail, Fluidd) on this host, not just the one being added.
    if sudo nginx -t >/dev/null 2>&1; then
        sudo systemctl reload nginx
        printf "[NGINX] Site installed and nginx reloaded. UI will be at http://<host>:%s/\n\n" "${MCU_UPDATER_UI_PORT}"
    else
        echo "[ERROR] Generated nginx config failed validation; rolling back."
        sudo nginx -t || true
        sudo rm -f "${enabled_dest}" "${site_dest}" "${confd_dest}"
        return 0
    fi
}

function restart_moonraker {
    echo "[MOONRAKER] Restarting Moonraker so the new config applies..."
    sudo systemctl restart moonraker
    printf "[MOONRAKER] Done.\n\n"
}

function print_next_steps {
    cat <<EOF
================================================================
 mcu-updater agent installed.

 Check it registered with Moonraker:

   curl -s http://localhost:7125/server/extensions/list

 ...should list an agent named "mcu_updater". Then try it:

   curl -s -X POST http://localhost:7125/server/extensions/request \\
     -H 'Content-Type: application/json' \\
     -d '{"agent":"mcu_updater","method":"fw.status","arguments":{}}'

 Logs:   ${PRINTER_DATA}/logs/mcu-updater.log
         (not in Mainsail's Logfiles panel - that lists a fixed set - but it is
          downloadable through Moonraker's file manager)
 Status: sudo systemctl status ${SERVICE_NAME}

 The CLI is unchanged and still works:  ${INSTALL_PATH}/mcu-updater.py status

 The systemd unit is 'mcu-updater', deliberately not 'klipper-*': KIAUH
 matches ^klipper(-[0-9a-zA-Z]+)?.service$ and would mistake it for a Klipper
 instance.

 The Mainsail fork (Vylyne/mainsail) is DEPRECATED - the standalone UI below
 is the supported client now. Existing fork installs keep working, but a new
 install should skip this step. See docs/mainsail-fork.md if you still need
 it:

   [update_manager mainsail]
   repo: Vylyne/mainsail        # was mainsail-crew/mainsail

 Config:    ${MAIN_CONFIG}     (backed up, editable in Mainsail)
              one file: the [updater] section and one [mcu ...] per board
 Artifacts: ${DATA_PATH}        (generated, not backed up)

 Flashing from the web UI is OFF by default. To enable it, add to the
 [updater] section of ${MAIN_CONFIG}:

   [updater]
   enable_flashing: true

 ...then: sudo systemctl restart ${SERVICE_NAME}

 Standalone UI: [update_manager mcu-updater-ui] tracks the latest release at
 ${UI_PATH} (see docs/standalone-ui.md). If none has been published yet, or
 the fetch failed, it still serves the placeholder - Moonraker's Update
 Manager will offer the real thing once a release exists; re-run install.sh
 to fetch it immediately instead of waiting for that panel. If you accepted
 the nginx prompt, it is reachable on port ${MCU_UPDATER_UI_PORT}. Re-run
 install.sh with UI_PATH/MCU_UPDATER_UI_PORT set to change either, or with
 MCU_UPDATER_UI_CHANNEL=beta to track beta releases (currently: ${MCU_UPDATER_UI_CHANNEL}).
================================================================
EOF
}

preflight_checks
check_paths
check_flash_deps
check_dfu_permissions
check_bootsel_permissions
check_config
migrate_legacy_service
install_service
add_asvc
add_update_manager
add_update_manager_ui
install_ui_release
allow_sudo_fallback
install_nginx_site
restart_moonraker
print_next_steps
