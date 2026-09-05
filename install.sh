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

# --- output ---------------------------------------------------------------
# Every line this script prints goes through the helpers below, so the layout
# is defined in one place instead of re-hand-aligned at each of a hundred call
# sites - which is how the tags drifted to different widths and the blank
# lines doubled up in the first place.
#
# Deliberately plain `printf` with no command substitution and no arithmetic:
# this file runs unattended under `set -eu` after every Moonraker update, and
# a helper that can fail would take the whole install down with it.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_HEAD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_OK=$'\033[32m'
    C_SKIP=$'\033[36m'
    C_WARN=$'\033[33m'
    C_ERR=$'\033[31m'
    C_OFF=$'\033[0m'
else
    C_HEAD=""
    C_DIM=""
    C_OK=""
    C_SKIP=""
    C_WARN=""
    C_ERR=""
    C_OFF=""
fi

# A phase header: one blank line before, none after. The status lines under it
# start immediately, which is what makes a section read as one block. The name
# is padded to a fixed width rather than the rule being computed to fill the
# line - a constant-width format string cannot fail, and arithmetic in here can.
function section {
    printf '\n%s── %-11s ─────────────────────────────────%s\n' \
        "${C_HEAD}" "$1" "${C_OFF}"
}

# Status lines. The word sits in a fixed eight-column gutter so every message
# starts at the same column, and `note` indents to that same width for
# continuation text. `step` is for something in progress whose result gets its
# own line afterwards.
function ok {
    printf '  %sok%s    %s\n' "${C_OK}" "${C_OFF}" "$*"
}

function skip {
    printf '  %sskip%s  %s\n' "${C_SKIP}" "${C_OFF}" "$*"
}

function warn {
    printf '  %swarn%s  %s\n' "${C_WARN}" "${C_OFF}" "$*"
}

function err {
    printf '  %serror%s %s\n' "${C_ERR}" "${C_OFF}" "$*"
}

function step {
    printf '  %s...%s   %s\n' "${C_DIM}" "${C_OFF}" "$*"
}

function note {
    printf '        %s%s%s\n' "${C_DIM}" "$*" "${C_OFF}"
}

# A yes/no prompt in the same gutter. The default is passed in rather than
# guessed: the two permission rules default to yes, the optional extras to no,
# and an empty answer (no tty, which is every Moonraker-triggered re-run) takes
# that default - exactly as each of these prompts behaved before.
function ask {
    local prompt="$1" default="$2" answer="" hint="[Y/n]"
    if [ "${default}" = "n" ]; then
        hint="[y/N]"
    fi
    read -r -p "  ?     ${prompt} ${hint}: " answer || answer=""
    if [ "${default}" = "n" ]; then
        case "${answer}" in
            y | Y | yes | YES) return 0 ;;
            *) return 1 ;;
        esac
    fi
    case "${answer}" in
        n | N | no | NO) return 1 ;;
        *) return 0 ;;
    esac
}

function preflight_checks {
    section "Checks"
    if [ "$EUID" -eq 0 ]; then
        err "this script must not be run as root."
        exit 1
    fi

    if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F 'moonraker.service')" ]; then
        ok "moonraker.service found"
    else
        err "moonraker.service not found. This agent is useless without it."
        exit 1
    fi

    if [ -z "${PYTHON_BIN}" ]; then
        err "python3 not found on PATH."
        exit 1
    fi

    # 3.11 is the floor: Raspberry Pi OS Bookworm ships it, and Trixie ships
    # 3.13. Note this is the *system* python3 by design - the agent runs under
    # it (see the unit's %PYTHON%), and katapult's flashtool.py is invoked with
    # sys.executable and needs apt's python3-serial, which a plain venv cannot
    # see. Do not "fix" this by pointing PYTHON_BIN at one.
    if ! "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        err "python3 >= 3.11 required, found $(${PYTHON_BIN} -V)"
        exit 1
    fi
    ok "$(${PYTHON_BIN} -V) at ${PYTHON_BIN}"
}

function check_paths {
    # Warnings, not errors: the agent is still worth having for status alone, and
    # the individual capabilities degrade rather than the whole thing failing.
    if [ ! -d "${KLIPPER_PATH}" ]; then
        warn "${KLIPPER_PATH} not found - klipper firmware cannot be built"
    fi
    if [ ! -f "${KATAPULT_PATH}/scripts/flashtool.py" ]; then
        warn "${KATAPULT_PATH}/scripts/flashtool.py not found - flashing unavailable"
    fi
    if [ ! -f "${KLIPPER_PATH}/lib/kconfiglib/kconfiglib.py" ]; then
        warn "vendored kconfiglib not found - the web config editor is unavailable"
    fi
    if [ ! -S "${PRINTER_DATA}/comms/moonraker.sock" ]; then
        warn "${PRINTER_DATA}/comms/moonraker.sock not present yet"
        note "The agent retries on a loop, so this resolves itself once Moonraker is up."
    fi
}

function check_flash_deps {
    # flashtool.py does `import serial`. Without pyserial the failure lands in the
    # middle of a flash, with klipper already stopped - so check it up front and
    # offer to fix it.
    if "${PYTHON_BIN}" -c 'import serial' >/dev/null 2>&1; then
        ok "pyserial present"
        return 0
    fi

    warn "python3-serial is missing"
    note "katapult's flashtool.py needs it, and without it a flash fails"
    note "part-way with klipper already stopped."
    if ! ask "Install python3-serial with apt now?" y; then
        skip "flashing will not work until you run: sudo apt install python3-serial"
        return 0
    fi

    if sudo apt-get install -y python3-serial; then
        if "${PYTHON_BIN}" -c 'import serial' >/dev/null 2>&1; then
            ok "python3-serial installed"
        else
            # e.g. PYTHON_BIN is a venv without system site-packages.
            warn "python3-serial installed, but ${PYTHON_BIN} cannot import it"
            note "Flashing will not work until that interpreter can."
        fi
    else
        warn "apt install failed - run 'sudo apt install python3-serial' yourself"
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
    section "Permissions"
    if ! command -v dfu-util >/dev/null 2>&1; then
        skip "DFU: dfu-util not installed - only needed for add-mcu on a bare board"
        return 0
    fi
    if [ -f "${DFU_UDEV_RULE}" ]; then
        skip "DFU: udev rule already present"
        return 0
    fi

    warn "DFU: no udev rule for STM32 DFU mode"
    note "${DFU_UDEV_RULE}"
    note "Without it dfu-util cannot open a board in DFU mode, and add-mcu"
    note "fails on a board whose boot jumper is perfectly fine."
    if ! ask "Install the DFU udev rule now?" y; then
        skip "DFU: add-mcu on a bare board will need sudo until you add it"
        return 0
    fi

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
            sudo usermod -aG plugdev "${USER}"
            ok "added ${USER} to the plugdev group"
            note "Log out and back in for your shell to pick that up."
        fi
    else
        warn "no plugdev group on this system; relying on TAG+=\"uaccess\""
    fi

    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=usb
    ok "DFU: udev rule installed"
    note "Replug a board in DFU mode for it to take effect."
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
        warn "USER is empty; skipping ${BOOTSEL_TMPFILES_CONF}"
        note "Substituting nothing would declare /media/BOOTSEL owned by no one."
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
        warn "could not read the tmpfiles.d template; skipping ${BOOTSEL_TMPFILES_CONF}"
        note "${INSTALL_PATH}/scripts/tmpfiles.d-mcu-updater-bootsel.conf"
        note "BOOTSEL flashing still works."
        return 0
    fi
    # tmpfiles.d reads % as a specifier prefix (%U is the UID), so a %USER% that
    # outlived the sed - a USER containing sed's & is how that happens - would
    # name a directory nobody meant to create. Belt and braces after the -n test.
    if grep -q '%USER%' "${tmp}"; then
        rm -f "${tmp}"
        warn "%USER% survived substitution; skipping ${BOOTSEL_TMPFILES_CONF}"
        note "systemd-mount creates the mountpoint parents either way, so BOOTSEL"
        note "flashing still works - they will just be root-owned."
        return 0
    fi
    if ! sudo install -m 0644 -o root -g root "${tmp}" "${BOOTSEL_TMPFILES_CONF}"; then
        rm -f "${tmp}"
        warn "could not install ${BOOTSEL_TMPFILES_CONF}; continuing"
        return 0
    fi
    rm -f "${tmp}"
    if ! sudo systemd-tmpfiles --create "${BOOTSEL_TMPFILES_CONF}"; then
        warn "systemd-tmpfiles --create ${BOOTSEL_TMPFILES_CONF} failed"
        note "The entry is installed and will be applied at next boot; until then"
        note "systemd-mount creates the parents itself, root-owned."
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
        skip "BOOTSEL: systemd-mount not found - only needed for add-mcu on a bare RP2040"
        return 0
    fi
    # Both files installed here are templated on ${USER}. An empty one does not
    # fail loudly: it would mount at /media//BOOTSEL and hand the volume to no
    # one, so refuse the whole thing rather than install a rule that misfires.
    if [ -z "${USER:-}" ]; then
        warn "BOOTSEL: USER is empty; skipping the udev rule and tmpfiles.d entry"
        note "Both are templated on it - see docs/bootsel-mountpoint-design.md."
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
        warn "BOOTSEL: no version marker in the shipped rule"
        note "${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules"
        note "Any already-installed rule will therefore look current, so a host"
        note "still on the old fixed-path rule would not be upgraded. Check the"
        note "file exists and carries its mcu-updater-bootsel-rule-version line."
    fi

    if [ -f "${BOOTSEL_UDEV_RULE}" ]; then
        have_rule=1
        installed="$(grep -m1 -o 'mcu-updater-bootsel-rule-version: [0-9]\+' \
            "${BOOTSEL_UDEV_RULE}" 2>/dev/null | grep -o '[0-9]\+' || true)"
        installed="${installed:-0}"
        if [ "${installed}" -ge "${shipped}" ]; then
            skip "BOOTSEL: udev rule already present (version ${installed})"
            # Self-heal only where consent already exists. A rule on disk means
            # this prompt was answered yes at some point; anyone who installed
            # between the rule's version bump and the tmpfiles.d entry being
            # wired in is current with no conf, and the version check alone
            # would never bring them one. No rule on disk means no consent, so
            # this never runs for someone who declined.
            if [ ! -f "${BOOTSEL_TMPFILES_CONF}" ]; then
                step "mountpoint parents not declared yet - installing ${BOOTSEL_TMPFILES_CONF}"
                install_bootsel_tmpfiles
                if [ "${BOOTSEL_TMPFILES_DONE:-0}" -eq 1 ]; then
                    ok "BOOTSEL: tmpfiles.d entry installed"
                fi
            fi
            return 0
        fi
        warn "BOOTSEL: udev rule is version ${installed}, shipped is ${shipped}"
        note "The old rule mounts every RP2040 at one fixed path, so two boards"
        note "in BOOTSEL collide and one spare board blocks flashing the other."
    else
        warn "BOOTSEL: no udev rule to mount an RP2040's BOOTSEL volume"
        note "${BOOTSEL_UDEV_RULE}"
        note "Without it nothing mounts the volume on a headless printer, and"
        note "add-mcu fails on a board whose BOOTSEL mode is perfectly fine."
    fi
    note "Installing writes that rule and ${BOOTSEL_TMPFILES_CONF} as root,"
    note "then runs udevadm and systemd-tmpfiles --create."
    if ! ask "Install the BOOTSEL udev rule and its tmpfiles.d entry now?" y; then
        if [ "${have_rule}" -eq 1 ]; then
            skip "BOOTSEL: keeping the version ${installed} rule"
            note "One board still mounts fine, but two RP2040s in BOOTSEL at once"
            note "collide on its single mountpoint, and one spare blocks the other."
        else
            skip "BOOTSEL: add-mcu on a bare RP2040 will need a manual mount"
        fi
        return 0
    fi

    local tmp
    tmp="$(mktemp)"
    # Not `sed ... > tmp` bare: the "could not read the template" warning above
    # is reachable (shipped=0), and this would otherwise abort the installer
    # right after it, taking install_service and restart_moonraker down with it.
    if ! sed -e "s|%USER%|${USER}|g" \
        "${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules" > "${tmp}" 2>/dev/null; then
        rm -f "${tmp}"
        warn "could not read the BOOTSEL rule template; skipping it"
        note "${INSTALL_PATH}/scripts/udev.d-mcu-updater-bootsel.rules"
        note "BOOTSEL flashing will need a manual mount."
        return 0
    fi
    sudo install -m 0644 -o root -g root "${tmp}" "${BOOTSEL_UDEV_RULE}"
    rm -f "${tmp}"

    sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=block

    install_bootsel_tmpfiles
    if [ "${BOOTSEL_TMPFILES_DONE:-0}" -eq 1 ]; then
        ok "BOOTSEL: udev rule and tmpfiles.d entry installed"
        note "Replug a board in BOOTSEL mode for it to take effect."
    else
        ok "BOOTSEL: udev rule installed"
        note "The tmpfiles.d entry was skipped for the reason above; the mountpoint"
        note "parents will just be root-owned. Replug a board for the rule to apply."
    fi
}

function check_config {
    section "Config"
    mkdir -p "${CONFIG_PATH}" "${DATA_PATH}"

    # A registry left at the pre-0.10 location would otherwise read as "no MCU
    # types configured", and the next add-type would write a fresh file while the
    # real one sat untouched. Refuse loudly instead.
    if [ -f "${HOME}/mcus/mcus.json" ] && [ ! -f "${MAIN_CONFIG}" ]; then
        err "found an old registry at ${HOME}/mcus/mcus.json but nothing at"
        note "${MAIN_CONFIG}"
        note "The layout moved - see docs/layout.md for the handful of commands."
        note "Refusing to continue so an empty registry cannot overwrite anything."
        exit 1
    fi

    # Same again for the previous split-file layout. Settings now live in the
    # [updater] section of the main config; a leftover updater.conf is no longer
    # read, and enable_flashing silently reverting to false is worth saying out
    # loud rather than leaving someone to wonder where the flash buttons went.
    if [ -f "${CONFIG_PATH}/updater.conf" ]; then
        warn "${CONFIG_PATH}/updater.conf is no longer read"
        note "Settings moved into the [updater] section of ${MAIN_CONFIG}."
        note "Copy anything you had set across, then delete it."
    fi
    if [ -f "${CONFIG_PATH}/mcus.cfg" ] && [ ! -f "${MAIN_CONFIG}" ]; then
        err "the registry is now ${MAIN_CONFIG}, not ${CONFIG_PATH}/mcus.cfg"
        note "Rename it (settings and the [mcu ...] sections share one file now):"
        note "    mv ${CONFIG_PATH}/mcus.cfg ${MAIN_CONFIG}"
        exit 1
    fi

    # A broken registry is surfaced here, loudly, rather than by the agent
    # reporting it as an error to the UI after the fact.
    if [ ! -f "${MAIN_CONFIG}" ]; then
        skip "no config at ${MAIN_CONFIG} yet - nothing to validate"
        return 0
    fi
    # The Python side prints facts, one per line, and this formats them - so the
    # gutter and the colours stay defined in exactly one place. A traceback would
    # be noise: the exception message already says what is wrong and how to fix
    # it, so print that and nothing else.
    local summary="" line=""
    if summary="$(PYTHONPATH="${INSTALL_PATH}/src" "${PYTHON_BIN}" -c '
import sys
from mcu_updater.config import Registry
from mcu_updater.errors import UpdaterError
from mcu_updater.paths import Paths
from mcu_updater.settings import load_settings
paths = Paths.from_env()
try:
    reg = Registry.load(paths)
    print(f"{len(reg)} MCU type(s), {len(reg.all_serials())} tracked serial(s)")
    # Same file, so a typo in [updater] is worth catching here too - the agent
    # would otherwise fall back to defaults with only a line in its log.
    s = load_settings(paths.settings_file)
    state = "ENABLED" if s.enable_flashing else "disabled"
    print(f"flashing from the web UI is {state}")
except UpdaterError as exc:
    print(f"{exc}", file=sys.stderr)
    sys.exit(1)
' 2>&1)"; then
        while IFS= read -r line; do
            if [ -n "${line}" ]; then
                ok "${line}"
            fi
        done <<< "${summary}"
    else
        err "the config at ${MAIN_CONFIG} is not loadable"
        while IFS= read -r line; do
            if [ -n "${line}" ]; then
                note "${line}"
            fi
        done <<< "${summary}"
        note "Fix that file, then re-run."
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
            step "removing the old ${legacy_name} service"
            sudo systemctl stop "${legacy_name}.service" 2>/dev/null || true
            sudo systemctl disable "${legacy_name}.service" 2>/dev/null || true
            sudo rm -f "${legacy_unit}"
            sudo systemctl daemon-reload
        fi

        if [ -f "${asvc}" ] && grep -qxF "${legacy_name}" "${asvc}"; then
            ok "dropped stale ${legacy_name} from moonraker.asvc"
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
            ok "renamed the ${legacy_name} update_manager entry"
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
                warn "moonraker.conf's update_manager path does not exist: ${declared}"
                note "Moonraker will error on that section. Expected ${INSTALL_PATH}."
            fi
        fi
    fi

    if [ "${backed_up}" -eq 1 ]; then
        ok "moonraker.conf updated (backup at ${conf}.bak-mcu-updater)"
    fi

    # An earlier install.sh used `sudo cp` from a mktemp file, so the unit could
    # be mode 0600 and unreadable to anything scanning /etc/systemd/system.
    local unit="/etc/systemd/system/${SERVICE_NAME}.service"
    if [ -f "${unit}" ] && [ ! -r "${unit}" ]; then
        sudo chmod 0644 "${unit}"
        ok "fixed permissions on ${unit}"
    fi
}

function install_service {
    section "Agent"
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
    ok "systemd unit ${SERVICE_NAME}.service installed and started"
}

function add_asvc {
    # Lets you restart the agent from Mainsail's own Services UI.
    section "Moonraker"
    local asvc="${PRINTER_DATA}/moonraker.asvc"
    if [ ! -f "${asvc}" ]; then
        skip "${asvc} not found - no allow-list entry"
        return 0
    fi
    if grep -qxF "${SERVICE_NAME}" "${asvc}"; then
        skip "${SERVICE_NAME} already in moonraker.asvc"
    else
        echo "${SERVICE_NAME}" >> "${asvc}"
        ok "added ${SERVICE_NAME} to moonraker.asvc"
    fi
}

function add_update_manager {
    local conf="${PRINTER_DATA}/config/moonraker.conf"
    if [ ! -f "${conf}" ]; then
        skip "${conf} not found - no update_manager entry"
        return 0
    fi
    if grep -q "^\[update_manager ${SERVICE_NAME}\]" "${conf}"; then
        skip "update_manager entry already present"
    else
        {
            printf "\n"
            cat "${INSTALL_PATH}/scripts/moonraker-update-manager.conf"
        } >> "${conf}"
        ok "added the update_manager entry to moonraker.conf"
    fi
}

function add_update_manager_ui {
    local conf="${PRINTER_DATA}/config/moonraker.conf"
    if [ ! -f "${conf}" ]; then
        skip "${conf} not found - no mcu-updater-ui update_manager entry"
        return 0
    fi
    if grep -q "^\[update_manager mcu-updater-ui\]" "${conf}"; then
        skip "mcu-updater-ui update_manager entry already present"
    else
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
        ok "added the mcu-updater-ui update_manager entry (channel: ${MCU_UPDATER_UI_CHANNEL})"
    fi
}

function install_ui_release {
    # Moonraker will not bootstrap an empty `type: web` directory - no
    # release_info.json means _is_valid=False, and it never downloads (see
    # docs/decisions.md). This performs that first fetch so the update
    # manager has something to compare against from the start.
    section "Web UI"
    if [ -f "${UI_PATH}/release_info.json" ]; then
        skip "${UI_PATH} already has a release installed"
        return 0
    fi

    if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
        skip "curl and unzip are required to fetch the UI release"
        return 0
    fi

    step "fetching the latest mcu-updater-ui release (channel: ${MCU_UPDATER_UI_CHANNEL})"
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
            skip "no stable release published yet - leaving the placeholder"
            note "Re-run install.sh once one exists, or track betas with"
            note "MCU_UPDATER_UI_CHANNEL=beta ./install.sh"
        else
            skip "no release published yet (or the fetch failed) - leaving the placeholder"
            note "Re-run install.sh once a release exists."
        fi
        return 0
    fi

    mkdir -p "${UI_PATH}"
    local tmp_zip
    tmp_zip="$(mktemp)"
    if ! curl -fsSL "${asset_url}" -o "${tmp_zip}"; then
        warn "download failed - leaving the placeholder"
        rm -f "${tmp_zip}"
        return 0
    fi

    # Unzip to a scratch directory first: a partial unzip must never leave
    # UI_PATH half-populated, since nginx may already be serving it.
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    if ! unzip -q -o "${tmp_zip}" -d "${tmp_dir}"; then
        warn "unzip failed - leaving the placeholder"
        rm -f "${tmp_zip}"
        rm -rf "${tmp_dir}"
        return 0
    fi
    rm -f "${tmp_zip}"

    find "${UI_PATH}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    cp -r "${tmp_dir}/." "${UI_PATH}/"
    rm -rf "${tmp_dir}"
    ok "UI release installed to ${UI_PATH}"
}

function allow_sudo_fallback {
    # The normal path needs no sudo: the agent stops klipper through Moonraker's
    # machine.services API. This is purely the safety net for Moonraker dying
    # between the stop and the start, when that API is unreachable and the agent
    # would otherwise be unable to bring klipper back.
    section "Safety net"
    local target="/etc/sudoers.d/mcu-updater"
    if [ -f "${target}" ]; then
        skip "sudoers fallback rule already installed"
        return 0
    fi

    note "The agent stops klipper via Moonraker, which needs no special privileges."
    note "But if Moonraker dies *between* the stop and the start, the agent cannot"
    note "put klipper back, and the printer stays down until you notice."
    note ""
    note "A narrow sudoers rule (three exact systemctl commands for the klipper"
    note "unit, no wildcards) lets it recover on its own. Declining is safe - the"
    note "unit's ExecStopPost still covers some cases - but the net is weaker, and"
    note "the CLI will prompt for a password when it stops klipper."
    if ! ask "Install ${target}?" n; then
        skip "no sudoers fallback - the agent relies on Moonraker being up"
        return 0
    fi

    local tmp
    tmp="$(mktemp)"
    sed -e "s|%USER%|${USER}|g" "${INSTALL_PATH}/scripts/sudoers.d-mcu-updater" > "${tmp}"
    # Validate before installing: a malformed sudoers file can lock you out of
    # sudo entirely, so never copy one in unchecked.
    if sudo visudo -c -f "${tmp}" >/dev/null 2>&1; then
        sudo install -m 0440 -o root -g root "${tmp}" "${target}"
        ok "installed ${target}"
    else
        err "the generated sudoers file failed validation; not installing it"
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
        skip "nginx not installed - no standalone UI site"
        return 0
    fi

    local site_dest="/etc/nginx/sites-available/${SERVICE_NAME}"
    local enabled_dest="/etc/nginx/sites-enabled/${SERVICE_NAME}"
    local confd_dest="/etc/nginx/conf.d/${SERVICE_NAME}.conf"

    if [ -f "${site_dest}" ]; then
        skip "nginx site already installed at ${site_dest}"
        return 0
    fi

    note "A dedicated nginx site for the standalone UI, served on its own port"
    note "alongside Mainsail/Fluidd rather than folded into either. Serves from"
    note "${UI_PATH} (override with UI_PATH before re-running)."
    if ! ask "Install the nginx site on port ${MCU_UPDATER_UI_PORT}?" n; then
        skip "no nginx site - the standalone UI is not served from this host"
        return 0
    fi

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
        ok "nginx site installed and reloaded - UI at http://<host>:${MCU_UPDATER_UI_PORT}/"
    else
        err "the generated nginx config failed validation; rolling back"
        sudo nginx -t || true
        sudo rm -f "${enabled_dest}" "${site_dest}" "${confd_dest}"
        return 0
    fi
}

function restart_moonraker {
    section "Finish"
    sudo systemctl restart moonraker
    ok "Moonraker restarted so the new config applies"
}

# What a person actually does next. The rationale that used to live here - why
# the unit is not called klipper-*, why the Mainsail fork is deprecated, how the
# standalone UI's channels work - is in the docs named below, where it can be
# read once rather than scrolled past after every update.
function print_next_steps {
    section "Installed"
    printf '\n'
    printf '  Verify the agent registered with Moonraker:\n'
    printf '    %scurl -s http://localhost:7125/server/extensions/list%s\n' "${C_DIM}" "${C_OFF}"
    printf '    %s(should list an agent named "mcu_updater")%s\n' "${C_DIM}" "${C_OFF}"
    printf '\n'
    printf '  Config     %s\n' "${MAIN_CONFIG}"
    printf '             %sone file: the [updater] section and one [mcu ...] per board%s\n' \
        "${C_DIM}" "${C_OFF}"
    printf '  Artifacts  %s\n' "${DATA_PATH}"
    printf '  Log        %s/logs/mcu-updater.log\n' "${PRINTER_DATA}"
    printf '  Status     sudo systemctl status %s\n' "${SERVICE_NAME}"
    printf '  CLI        %s/mcu-updater.py status\n' "${INSTALL_PATH}"
    printf '  Web UI     http://<host>:%s/  %s(docs/standalone-ui.md)%s\n' \
        "${MCU_UPDATER_UI_PORT}" "${C_DIM}" "${C_OFF}"
    printf '\n'
    printf '  Flashing from the web UI is OFF by default. To enable it, add to the\n'
    printf '  [updater] section of the config above:\n'
    printf '    %s[updater]%s\n' "${C_DIM}" "${C_OFF}"
    printf '    %senable_flashing: true%s\n' "${C_DIM}" "${C_OFF}"
    printf '  then: sudo systemctl restart %s\n' "${SERVICE_NAME}"
    printf '\n'
    printf '  %sThe Mainsail fork (Vylyne/mainsail) is deprecated - see docs/mainsail-fork.md%s\n' \
        "${C_DIM}" "${C_OFF}"
    printf '\n'
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
