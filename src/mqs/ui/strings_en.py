"""English UI texts. Same names as mqs.ui.strings_cs, which is the source; see mqs.ui.strings."""

from __future__ import annotations

APP_TITLE = "MikroTik QuickSet Plus"

NAV = {
    "device": "Device",
    "network": "Batch update",
    "basic": "Basics",
    "lan": "LAN / Bridge",
    "firewall": "Firewall",
    "services": "Services and hardening",
    "preview": "Preview",
    "apply": "Apply",
}

# status bar / header
DISCONNECTED = "Disconnected"
CONNECTED = "Connected"
OFFLINE_DIALECT = "Offline – target RouterOS {version}"
DIALECT_BADGE = "RouterOS v{major} ({version})"
SECURITY = {
    "anonymous-dh": "api-ssl: peer identity not verified",
    "verified": "api-ssl: verified certificate",
    "ssh-host-key": "SSH: key fingerprint {fingerprint}",
    "unknown": "",
}

# device page
DEVICE_CONNECT_GROUP = "Connection"
DEVICE_HOST = "IP address / hostname"
DEVICE_USER = "User"
DEVICE_PASSWORD = "Password"
DEVICE_KEY = "SSH key (optional)"
DEVICE_KEY_PASSPHRASE = "SSH key passphrase"
DEVICE_KEY_PASSPHRASE_HINT = "only for an encrypted key"
DEVICE_TRANSPORT = "Transport"
TRANSPORTS = {"auto": "API-SSL, SSH when unavailable", "api-ssl": "API-SSL only (8729)",
              "ssh": "SSH only"}
DEVICE_SSH_PORT = "SSH port"
DEVICE_API_PORT = "API-SSL port"
DEVICE_CA = "CA certificate (optional)"
DEVICE_REMEMBER_LOGIN = "Remember login details"
DEVICE_REMEMBER_LOGIN_TIP = (
    "On the next start the form is filled in as it was left, password included. "
    "Stored encrypted (Windows DPAPI) for this Windows user only."
)
DEVICE_CONNECT = "Connect"
DEVICE_DISCONNECT = "Disconnect"
DEVICE_CANCEL = "Abort"
DEVICE_SCAN_GROUP = "Devices on the network (MNDP)"
DEVICE_SCAN_START = "Search the network"
DEVICE_SCAN_STOP = "Stop"
DEVICE_SCAN_COLUMNS = ["Identity", "IP address", "MAC address", "Version", "Model",
                       "Network adapter", "Device interface"]
DEVICE_SCAN_HINT = ("Double-click a device to take it into the form. Windows Firewall may "
                    "block incoming UDP 5678.")
DEVICE_USE_MAC = "Connect by MAC"
DEVICE_MAC_HINT = ("Select a device in the table. IPv6 link-local is tried first, otherwise "
                   "MAC-Telnet: a temporary IP is added on the device and the connection goes "
                   "through it. Not verified on a device.")
MAC_CONFIRM_TITLE = "Connect by MAC address"
MAC_CONFIRM_TEXT = (
    "Device {identity} (MAC {mac}) has no usable IP. IPv6 link-local is tried first; if it "
    "does not answer, I log in over MAC-Telnet and add a temporary address {address} "
    "(comment mqs-temp) on interface {interface}, then connect through it. The interface "
    "name comes from MNDP and may be stale after a rename, so it is checked on the device "
    "after logging in. The same address is lent the same way on a link-local connection too "
    "when a file cannot be transferred otherwise – a RouterOS package does not fit through "
    "the MAC-Telnet console. On disconnect the address is removed again. After the "
    "configuration is applied I reconnect the same way by MAC, temporary address included."
    "\n\nVerified on RouterOS 7.20.7. Continue?"
)
CONNECTED_BY_MAC = "Connected: {mac} (via {method}; {transport} at {host})"
MAC_TEMP_ADDRESS = ("temporary address on the device: {address} (comment mqs-temp; removed on "
                    "disconnect, a reset deletes it by itself)")
MAC_RETURNING = "Disconnecting – removing temporary address {address}…"
MAC_RETURNED = "Disconnected – temporary address {address} removed"
MAC_NOT_RETURNED = ("Disconnected, but temporary address {address} stayed on the device. "
                    "Remove it by hand (IP → Addresses, comment mqs-temp).")
MAC_NO_INTERFACE = ("Could not find out which network adapter reaches the device. "
                    "Run the search again.")
MAC_NO_ADDRESS = "There is no free address in this adapter's network that could be lent."
DEVICE_INFO_GROUP = "Detection"
DEVICE_NOTES = "Notes on the model (from MikroTik documentation, not verified on a device):"
DEVICE_PORTS_GROUP = "Ports"
DEVICE_PORT_COLUMNS = ["Port", "default-name", "Type", "Switch chip", "State", "MAC"]
DEVICE_OFFLINE_GROUP = "Offline (no device)"
DEVICE_OFFLINE_VERSION = "Target RouterOS version"
DEVICE_OFFLINE_PORTS = "Ports (comma separated)"
DEVICE_OFFLINE_IPV6 = "ipv6 package (v6)"
IPV6_STATES = {"unknown": "unknown", "enabled": "installed and enabled",
               "disabled": "disabled", "missing": "missing"}
INFO_FIELDS = {
    "version": "Version", "board": "Model (board)", "architecture": "Architecture",
    "uptime": "Uptime", "memory": "Free memory", "identity": "Identity",
    "routerboard": "RouterBOARD / firmware", "wireless": "Wi-Fi", "ipv6": "ipv6 package",
}

# basic page
BASIC_IDENTITY = "Device identity"
BASIC_MODE = "Mode (prefills the form)"
MODES = {"router": "Router", "bridge-ap": "Bridge / AP", "custom": "Custom"}
MODE_APPLY = "Prefill"
PORT_NAMES_GROUP = "Port names"
PORT_NAMES_COLUMNS = ["Port (default-name)", "Name", "Now on the device"]
PORT_NAMES_HELP = (
    "Double-click a port name to edit it. The script renames the ports first "
    "(/interface ethernet set [ find default-name=… ] name=…), so the name holds after a "
    "reset too. Names from a connected device are taken into the profile by themselves."
)
PORT_NAME_EMPTY = "A port name must not be empty."
PORT_NAME_TAKEN = "Port {port} already has the name {name}."
WAN_GROUP = "WAN"
WAN_ENABLED = "Use WAN"
WAN_INTERFACE = "Interface"
WAN_VLAN = "WAN is in a VLAN (tagged traffic on the port)"
WAN_VLAN_ID = "VLAN ID"
WAN_VLAN_NAME = "VLAN interface name"
WAN_VLAN_HELP = (
    "Cetin delivers PPPoE in VLAN 848: turn this on and keep 848. A vlan848 interface is "
    "created on the WAN port and the WAN (PPPoE, DHCP or a static address) runs on it — the "
    "port itself stays without an IP address. Leave the name empty to name it after the ID "
    "(vlan848)."
)
WAN_TYPE = "Type"
WAN_TYPES = {"dhcp": "DHCP client", "static": "Static IP", "pppoe": "PPPoE"}
WAN_ADDRESS = "Address / mask (CIDR)"
WAN_GATEWAY = "Gateway"
WAN_DNS = "DNS (optional, comma separated)"
WAN_PPPOE_USER = "PPPoE user"
WAN_PPPOE_PASSWORD = "PPPoE password"
WAN_PPPOE_NAME = "Interface name"
WAN_LIST = "Add to the WAN interface list"
WAN_MSS = "MSS clamping"
WAN_NAT = "NAT masquerade on WAN"

# LAN / bridge page
BRIDGE_GROUP = "Bridge"
BRIDGE_NAME = "Bridge name"
BRIDGE_PROTOCOL = "protocol-mode"
BRIDGE_PRIORITY = "Priority (optional)"
BRIDGE_AGEING = "ageing-time [s] (optional)"
BRIDGE_VLAN = "vlan-filtering"
BRIDGE_PVID = "PVID"
BRIDGE_FRAME_TYPES = "frame-types"
BRIDGE_IP_FIREWALL = "use-ip-firewall (turns HW offload off)"
BRIDGE_PORTS = "Member ports (the WAN port is left out automatically)"
OFFLOAD_GROUP = "Bridge hardware offload"
OFFLOAD_MODES = {"auto": "Auto (by detection)", "force-on": "Force on", "off": "Off"}
OFFLOAD_COLUMNS = ["Port", "Switch chip", "Offload", "Reason"]
OFFLOAD_STATUS = {"yes": "YES", "no": "NO", "uncertain": "uncertain", "not-offered": "—"}
OFFLOAD_HELP = (
    "HW offload switches traffic between ports right in the switch chip. Ports on different "
    "chips are not offloaded between each other – traffic between the groups goes through "
    "the CPU. It does not depend on FastTrack."
)
OFFLOAD_SWITCH_TO_RSTP = "Switch to rstp"
LAN_GROUP = "LAN IP address"
LAN_ADDRESSING = "Bridge addressing"
LAN_ADDRESSINGS = {"static": "Static", "dhcp-client": "DHCP client", "none": "None"}
LAN_ADDRESS = "Address / mask (CIDR)"
LAN_GATEWAY = "Gateway (default route, optional)"
LAN_HELP = (
    "In Bridge / AP mode choose Static if the device should have a fixed management address. "
    "The gateway creates a default route (/ip route add dst-address=0.0.0.0/0) so the device "
    "reaches DNS, NTP and SNMP outside its own network. With WAN on, WAN adds the default "
    "route already."
)
DHCP_GROUP = "DHCP server"
DHCP_ENABLED = "Enable DHCP server"
DHCP_POOL_START = "Pool from"
DHCP_POOL_END = "Pool to"
DHCP_GATEWAY = "Gateway (empty = bridge address)"
DHCP_DNS = "DNS (empty = automatic)"
DHCP_DNS_HELP = (
    "Empty: the bridge address when remote DNS requests are allowed, otherwise the DNS "
    "servers from Services and hardening."
)
DHCP_LEASE = "Lease time [s]"
DHCP_NAME = "Server name"
DHCP_COMMENT = "Network name (comment)"

# firewall page
FIREWALL_BASIC = "Deploy the basic filter"
FIREWALL_BASIC_HELP = (
    "input: accept established/related/untracked, drop invalid, accept ICMP, accept from LAN, "
    "(management from WAN), drop everything not from LAN. forward: established/related/"
    "untracked, drop invalid, drop new connections from WAN that are not dst-nat. The rules "
    "carry the comment “quickset: …”; applied to a running configuration, those from an "
    "earlier run are replaced and the filter is placed above the rules already on the device."
)
FIREWALL_WAN_MANAGEMENT = "Allow management from WAN (api-ssl, SSH, Winbox, SNMP, discovery)"
FIREWALL_WAN_MANAGEMENT_HELP = (
    "Opens the service ports from WAN as the hardening block sets them (e.g. SSH 122). "
    "Disabled services are not opened. With a management subnet set, the rules accept only "
    "from it. With IPv6 on it applies to the IPv6 filter too (with a management subnet "
    "nothing is opened over IPv6)."
)
FIREWALL_IPV6 = "Enable IPv6 (overrides disable-ipv6=yes from the hardening block)"
FIREWALL_IPV6_HELP = (
    "Sets /ipv6 settings disable-ipv6=no and the hardening block no longer turns it off. Adds "
    "an IPv6 filter even without the basic filter. input: accept established/related/"
    "untracked, drop invalid, accept ICMPv6, loopback, DHCPv6 client on WAN, accept from LAN, "
    "(management from WAN), drop everything not from LAN. forward: established/related/"
    "untracked, drop invalid, accept ICMPv6, drop new connections from WAN. On v6 the ipv6 "
    "package has to be enabled, otherwise nothing is generated."
)
FASTTRACK_GROUP = "FastTrack"
FASTTRACK_ENABLED = "Enable FastTrack"
FASTTRACK_HW = "hw-offload on the FastTrack rule (only when the device supports it)"
FASTTRACK_HELP = (
    "FastTrack bypasses most packet processing in the CPU and so speeds up routing.\n"
    "• Simple queues and HTB do not see fasttracked traffic.\n"
    "• Traffic accounting and per-connection statistics are skewed.\n"
    "• Mangle rules do not apply to fasttracked packets. MSS clamping on SYN packets keeps "
    "working, because those are not fasttracked.\n"
    "• Works with a PPPoE WAN, but without hardware acceleration."
)
FASTTRACK_SHAPING_QUESTION = (
    "Do you use traffic shaping on the device (simple queues or queue tree / HTB)?\n\n"
    "FastTrack would bypass it."
)
FASTTRACK_SHAPING_TITLE = "FastTrack and traffic shaping"

# services page
SERVICES_FIELDS_GROUP = "Most often changed values"
SERVICES_DNS = "DNS servers"
SERVICES_DNS_REMOTE = "Allow remote DNS requests (allow-remote-requests)"
SERVICES_DNS_REMOTE_HELP = (
    "On: the router answers DNS requests from LAN and the DHCP server hands out the bridge "
    "address as DNS. Off: the DHCP server hands out the DNS servers above. It can be turned "
    "on only with the basic firewall filter."
)
SERVICES_SSH_PORT = "SSH port"
SERVICES_SNMP = "SNMP community"
SERVICES_NTP = "NTP servers"
SERVICES_TZ = "Time zone"
SERVICES_EDITOR_GROUP = "Hardening block (RouterOS v7 syntax, translated for v6)"
SERVICES_RESET = "Restore default"
SERVICES_RESET_CONFIRM = "Overwrite the hardening block with the default company template?"
SERVICES_ROBUST_LOGGING = "Robust logging variant (find by topics instead of indexes)"
SERVICES_EXTENSIONS_GROUP = "Optional extensions"
SERVICES_CERTIFICATE = "Assign a certificate to api-ssl"
SERVICES_MGMT_SUBNET = "Restrict ssh, winbox and api-ssl to a subnet"
ACCESS_OK = "Access paths after applying: {paths}"
ACCESS_BLOCKED = "No access path would remain after applying – applying is blocked."

# preview page
PREVIEW_V6 = "v6"
PREVIEW_V7 = "v7"
PREVIEW_BOTH = "side by side"
PREVIEW_COPY = "Copy"
PREVIEW_SAVE = "Save .rsc"
PREVIEW_COPIED = "Script copied to the clipboard."
PREVIEW_SAVE_FILTER = "RouterOS script (*.rsc)"
PREVIEW_ISSUES = "Warnings"

# apply page
APPLY_MODE_GROUP = "Mode"
APPLY_MODES = {
    "generate": "Generate only (no network)",
    "import": "Apply to the running configuration (/import)",
    "reset": "Reset + apply (run-after-reset)",
}
APPLY_ROLLBACK = "Rollback safeguard: restore the backup after [min] unless I confirm"
APPLY_DOWNLOAD = "Download backup and export to folder"
APPLY_KEEP_BACKUPS = "Keep the latest backups on the device"
APPLY_KEEP_BACKUPS_ALL = "all"
APPLY_RESET_DELAY = "Delay after boot [s]"
APPLY_KEEP_USERS = "Keep users (keep-users)"
APPLY_KEEP_USERS_TIP = (
    "The reset keeps users and their passwords. After the reset the application logs in "
    "with the same account and changes the admin password only if you enter one."
)
APPLY_ADMIN_PASSWORD = "New password for user admin (after reset)"
APPLY_ADMIN_PASSWORD_KEEP = "optional – empty keeps the current one"
APPLY_UPDATE = "Update RouterOS from the Update folder (on reset)"
APPLY_UPDATE_TIP = (
    "Before the reset the RouterOS package for the device's architecture is uploaded from "
    "the Update folder and the reboot after the reset installs it. The newest version in "
    "the folder is used, only if it is newer than the one on the device. The pre-apply "
    "checks show what will be installed."
)
APPLY_UPDATE_DIR = "Folder: {dir}"
APPLY_ROUTERBOOT = "Update RouterBOOT (one more reboot)"
APPLY_ROUTERBOOT_TIP = (
    "After the configuration is verified, or after a move to another RouterOS version, "
    "RouterBOOT is updated to the firmware carried by the RouterOS on the device, if it is "
    "newer than the running one (even without a RouterOS update). The device reboots once "
    "more for it."
)
APPLY_ROUTERBOOT_DOWN = "Lower RouterBOOT when it is newer than RouterOS"
APPLY_ROUTERBOOT_DOWN_TIP = (
    "Writes the firmware carried by the RouterOS on the device even when it is older than "
    "the running RouterBOOT – the state after a RouterOS downgrade. The device can do it and "
    "it is reversible, but it is not needed: an older RouterOS runs under a newer RouterBOOT "
    "(verified 6.49.21 under 7.23.7). It never happens by itself; a RouterOS downgrade does "
    "not lower the bootloader."
)
APPLY_VERSION = "Move to another RouterOS version (on its own, without a reset)"
APPLY_VERSION_TIP = (
    "Installs the version you pick from the Update folder: a newer one by reboot, an older "
    "one with /system package downgrade. The device configuration stays as it is – set it "
    "up again after the move (Reset + apply). It cannot go below the version the board was "
    "made with."
)
APPLY_VERSION_BUTTON = "Move to version…"
APPLY_VERSION_FLOOR = "Lowest possible version: {floor}"
APPLY_VERSION_FLOOR_UNKNOWN = "The device does not say which version it was made with."
APPLY_VERSION_CURRENT = "{version} – version on the device"
APPLY_VERSION_BLOCKED = "{version} – not possible"
APPLY_VERSION_NONE = "The Update folder has no package for this device"
APPLY_VERSION_DONE = "The device has RouterOS {version}. Set up the configuration again now."
APPLY_VERSION_FAILED = "The move to another version did not finish: {error}"
APPLY_PREFLIGHT = "Pre-apply checks"
APPLY_DRY_RUN = "Dry-run and apply…"
APPLY_BUTTON = "Apply"
APPLY_CONFIRM_WORKS = "Confirm the configuration works"
APPLY_CHECKLIST = "Result"
APPLY_LOG = "Progress"
APPLY_CHECK_OK = "OK"
APPLY_CHECK_FAIL = "ERROR"
DRY_RUN_TITLE = "Dry-run – exactly this is sent to the device"
DRY_RUN_CONFIRM = "Understood, apply"
DIALECT_MISMATCH_CONFIRM = "Apply the script for another RouterOS major version anyway"
VERSION_TITLE = "Move to another RouterOS version"
VERSION_TEXT = (
    "RouterOS {version} will be installed on device {identity} (now {current}). "
    "Reboots: {reboots}. Check the settings after the move; the device may not come back "
    "on the same address.\n\nTo confirm, type the device identity:"
)
RESET_TITLE = "Reset the device configuration"
RESET_TEXT = (
    "Device {identity} is reset without the default configuration, rebooted, and after "
    "boot it imports the uploaded script. {users}\n\nTo confirm, type the device identity:"
)
RESET_USERS_DELETED = "All users except admin will be deleted."
RESET_USERS_KEPT = "Users and their passwords are kept."

# profiles
MENU_PROFILE = "Profile"
PROFILE_NEW = "New"
PROFILE_SAVE = "Save"
PROFILE_SAVE_AS = "Save as…"
PROFILE_OPEN = "Open…"
PROFILE_IMPORT = "Import from file…"
PROFILE_EXPORT = "Export to file…"
PROFILE_NAME_PROMPT = "Profile name:"
PROFILE_FILTER = "QuickSet Plus profile (*.json)"

# view
MENU_VIEW = "View"
VIEW_SHOW_PASSWORDS = "Show passwords"
PASSWORD_SHOW = "Show password"
PASSWORD_HIDE = "Hide password"
VIEW_LANGUAGE = "Language"
LANGUAGE_AUTO = "Automatic (by Windows)"
LANGUAGE_RESTART_TITLE = "Language"
LANGUAGE_RESTART = ("The language changes after the application restarts. Restart now?\n\n"
                    "The window closes and opens again; the login form is kept.")
LANGUAGE_RESTART_BUSY = ("The language changes after the application restarts. Something is "
                         "still running – restart it yourself once it finishes.")

# help / application update
MENU_HELP = "Help"
UPDATE_CHECK = "Check for updates…"
UPDATE_AUTO = "Check for updates on start"
UPDATE_ABOUT = "About"
UPDATE_ABOUT_TEXT = ("{app} {version}\n\nGenerates and applies a baseline MikroTik "
                     "RouterOS v6/v7 configuration.\n\n{url}")
UPDATE_TITLE = "Application update"
UPDATE_AVAILABLE = "Version {version} is available (you have {current})."
UPDATE_NOTES = "What is new:"
UPDATE_INSTALL = "Download and install"
UPDATE_OPEN_PAGE = "Open the release page"
UPDATE_SKIP = "Skip this version"
UPDATE_LATER = "Later"
UPDATE_NOT_FROZEN = ("Running from the sources: the update is not installed here. "
                     "Pull the new version with git, or download the exe from the release "
                     "page.")
UPDATE_NOT_WRITABLE = ("The folder with the exe is not writable ({dir}), so the update cannot "
                       "replace it. Download it from the release page.")
UPDATE_NO_DIGEST = ("The release does not publish a SHA-256 checksum of the file, so the "
                    "download could not be verified. Download it from the release page.")
UPDATE_UP_TO_DATE = "You have the latest version ({version})."
UPDATE_CHECK_FAILED = "Could not check for updates: {error}"
UPDATE_DOWNLOADING = "Downloading version {version}…"
UPDATE_DOWNLOAD_FAILED = "The update could not be downloaded: {error}"
UPDATE_BUSY = ("Something is still running (connecting, applying, a batch). Install the "
               "update once it finishes.")
UPDATE_READY = ("Version {version} is downloaded and verified. The application now closes "
                "(the device is disconnected) and starts again in the new version.")
UPDATE_INSTALL_FAILED = "The update could not be installed, the current version stays: {error}"

# input syntax (checked in the UI before values reach the model)
INVALID_CIDR = "Enter an address as a.b.c.d/nn"
INVALID_IP = "Enter an IPv4 address a.b.c.d"
INVALID_IP_LIST = "Enter IPv4 addresses separated by commas"
INVALID_NETWORK = "Enter a network as a.b.c.d/nn"
INVALID_NUMBER = "Enter a whole number {minimum}–{maximum}"
INVALID_RANGE = "The pool start must be lower than or equal to its end"
WAN_PORT_LOCKED = "WAN port"

# connection errors / session
CONNECT_AUTH_FAILED = "Login failed – check the user and password."
CONNECT_KEY_PASSPHRASE = "The private key is encrypted – fill in “SSH key passphrase”."
CONNECT_KEY_PASSPHRASE_WRONG = "The SSH key passphrase is wrong – the key could not be opened."
CONNECT_FAILED = "Could not connect with any transport:\n{details}"
CONNECT_CANCELLED = "Connecting was aborted."
CONNECT_UNSUPPORTED = "RouterOS {version} is not supported (supported: 6.40+ and 7.x)."
CONNECT_FELL_BACK = "api-ssl is not available, connected over SSH."
CONNECT_PROGRESS = "Connecting and reading device information…"
SCAN_ERRORS = "Some network interfaces cannot be listened on: {errors}"
APPLY_NOT_CONNECTED = "Connect to a device first to apply the configuration."
APPLY_DONE = "Configuration applied."
APPLY_DONE_WITH_WARNINGS = "Configuration applied, but some checks failed."
APPLY_DONE_DISCONNECTED = "Configuration applied, device disconnected."
APPLY_CONFIRMED_DISCONNECTED = "Configuration confirmed, device disconnected."
APPLY_SAVE_SCRIPT = "Save .rsc…"

# generic
ERROR_TITLE = "Error"
WARNING_TITLE = "Warning"
YES = "Yes"
NO = "No"

# --- Batch update page (device search and batch update) ----------------------------------------
NET_PAGE_TITLE = "Batch update"
NET_SCAN_GROUP = "Searching for devices on the network"
NET_SCAN_START = "Search the network"
NET_SCAN_STOP = "Stop"
NET_SECONDS = "Listen to MNDP (s):"
NET_EXPORT = "Export table…"
NET_SCAN_PORTS = "Scan ports"
NET_SCAN_PORTS_HINT = ("Tries ports 8729, 122, 22 and 8291 in the given networks. Off, "
                       "because it also touches devices nobody asked about – and an open "
                       "port proves nothing by itself. Limited to 50 addresses/s.")
NET_SUBNETS = "Networks:"
NET_SNMP = "SNMP community:"
NET_SNMP_HINT = ("Reads sysObjectID over SNMPv2c in the given networks. Empty = SNMP is "
                 "not used.")
NET_NEEDS_SUBNET = ("Both port scanning and SNMP need at least one network in the "
                    "“Networks” field (e.g. 192.168.55.0/24).")
NET_TABLE_GROUP = "Devices found"
NET_LEGEND = ("State: ++ confirmed (logged in, data from the device – only these can be "
              "updated), + probably MikroTik, ? answers, but nothing points to MikroTik. "
              "Bold row = changed since the last scan, details in the tooltip. Selected rows "
              "(Ctrl/Shift + click) decide which devices are logged in to and which go into "
              "the batch; with nothing selected every confirmed device goes into the batch.")
NET_STATE_CONFIRMED = "++"
NET_STATE_PROBABLE = "+"
NET_STATE_UNKNOWN = "?"
NET_ORDER_GROUP = "Batch update (from the most distant devices towards the backbone)"
NET_NOT_CONFIRMED = "not confirmed, will not go into the batch"
NET_ORDER_OK = "Order built from registration and neighbor tables: {count} devices."
NET_LOGIN = "Log in to selected"
NET_LOGIN_TIP = ("Logs in to the selected devices and reads their state, neighbors and "
                 "registration tables. Only then can a device be updated and the order be "
                 "built. It deliberately does not log in to everything the scan heard – on "
                 "someone else's network it would be someone else's device too.")
NET_LOGIN_WITH = ("Login details from the Device page: {user} ({auth}), "
                  "API-SSL {api}, SSH {ssh}.")
NET_LOGIN_KEY = "key"
NET_LOGIN_PASSWORD = "password"
NET_LOGIN_NONE = "No user is filled in on the Device page – nothing to log in with."
NET_LOGGING_IN = "Logging in to {count} devices…"
NET_LOGIN_STEP = "Logging in: {name} – {state}"
NET_LOGIN_STATES = {"connecting": "connecting", "confirmed": "read",
                    "failed": "not logged in"}
NET_LOGIN_DONE = "Read {ok} devices, not logged in {failed}."
NET_LOGIN_FAILED = "Logging in failed: {error}"
NET_LOGIN_REMEMBERED = "{name}: logged in as {user} (details stored for the device)"
NET_REMEMBERED = ("{count} devices have stored details – those are tried first, then the "
                  "details from the Device page.")
NET_REMEMBERED_MEMORY = "Only until the window closes, not stored on disk."
NET_REMEMBERED_UNREADABLE = "The stored login details cannot be read ({error})."
NET_REMEMBER_FAILED = "The login details could not be stored: {error}"
NET_FORGET = "Forget stored details"
NET_FORGET_TIP = ("Each device remembers the user and password it last logged in with, "
                  "encrypted under this Windows account. The button deletes all of them.")
NET_FORGET_CONFIRM = "Delete the stored login details ({count} devices)?"
NET_NO_ADDRESS = "the device has no IP address"
NET_TARGET = "Target version:"
NET_TARGET_TIP = ("The RouterOS version the devices should move to. The versions offered "
                  "are those in the Update folder ({dir}).")
NET_HERE = "This PC is behind:"
NET_HERE_TIP = ("The device this computer is connected to the network through. It goes "
                "last in the batch regardless of the order, because its reboot ends the "
                "batch.")
NET_HERE_UNKNOWN = "(don't know)"
NET_PARALLEL = "At once:"
NET_PARALLEL_TIP = ("How many devices are updated at the same time. A device with something "
                    "behind it always goes alone. Keep 1 on the backbone.")
NET_ROUTERBOOT = "RouterBOOT"
NET_ROUTERBOOT_TIP = (
    "After the move to the target version RouterBOOT is updated to the firmware carried by "
    "the RouterOS on the device, if it is newer than the running one. A device that already "
    "has the target version and a newer RouterBOOT waiting goes into the batch too – only for "
    "RouterBOOT, with one reboot and without touching the configuration."
)
NET_ROUTERBOOT_DOWN = "Lower RouterBOOT"
NET_WAIT_STATIONS = "Wait for stations to return"
NET_WAIT_STATIONS_TIP = (
    "After the last version an AP waits (5 minutes at most) for the stations that were "
    "connected to it before the reboot. Stations that do not return stop the batch. Off: the "
    "AP is done as soon as its radios are up, and the batch goes on regardless of stations."
)
NET_BATCH_START = "Start batch"
NET_BATCH_STOP = "Stop batch"
NET_BATCH_STOP_TIP = ("The batch stops after the device being updated right now – a move "
                      "in progress is not interrupted.")
NET_PLAN_DEVICE = "{number}. {name}  ({role}, now {current})"
NET_PLAN_BEHIND = ", {count} behind it"
NET_PLAN_FLOOR = "     lowest possible version: {floor}"
NET_PLAN_FLOOR_UNKNOWN = "     the device does not state its lowest possible version"
NET_PLAN_HOP = "     → {version}: {files}"
NET_PLAN_DRIVERS = "     radio driver: {drivers}"
NET_PLAN_CONFIRM = "     CAUTION: downgrade or a move between RouterOS 6 and 7"
NET_PLAN_UNCHANGED = "     already has {target}, nothing is installed"
NET_PLAN_ROUTERBOOT = "     already has {target}, only RouterBOOT {current} → {version}"
NET_PLAN_ROUTERBOOT_DOWN = ("     already has {target}, only lowering RouterBOOT {current} → "
                            "{version}")
NET_PLAN_BLOCKED = "     NOT POSSIBLE: {detail}"
NET_PLAN_LIST = "{number}. {name}  ({summary})"
NET_ROLES = {"ap": "AP", "station": "station", "none": "no radio", "unknown": "?"}
NET_TREE = "Links:"
NET_TREE_LINK = "  {parent}{through} → {child}"
NET_TREE_ORPHAN = "  {name}: no link to the others"
NET_BATCH_BLOCKED = ("The batch will not start: {names} has no usable plan. Remove them "
                     "from the selection, or add packages to the Update folder.")
NET_BATCH_NOTHING = "All selected devices already have {target}."
NET_BATCH_READY = "{count} devices go into the batch, {reboots} reboots in total."
NET_BATCH_TITLE = "Batch update"
NET_BATCH_CONFIRM = ("Update {count} devices to RouterOS {target}?\n\n"
                     "Order: {names}\nReboots in total: {reboots}\n\n"
                     "A configuration backup is downloaded before each move. The batch stops "
                     "at the first device that does not come back or fails a check.")
NET_BATCH_CONFIRM_FIRMWARE = ("RouterBOOT only (RouterOS already has the target version, one "
                              "reboot): {names}")
NET_BATCH_WHY = "The batch has to be confirmed by hand: {reasons}"
NET_BATCH_WHY_ORDER = "the order is not certain (incomplete topology)"
NET_BATCH_WHY_VERSION = "downgrade or a move between RouterOS 6 and 7 ({names})"
NET_BATCH_TYPE = "To confirm, type the target version {target}:"
NET_BATCH_RUNNING = "The batch is running…"
NET_BATCH_STOPPING = "The batch stops after the current device…"
NET_BATCH_STEP_RUNNING = "▶ {name}"
NET_BATCH_STEP_DONE = "OK {name}"
NET_BATCH_STEP_FAILED = "ERROR {name}: {detail}"
NET_BATCH_DONE = "Batch done: {count} devices updated."
NET_BATCH_CANCELLED = "Batch stopped: {done} updated, {rest} not started."
NET_BATCH_FAILED = "The batch failed: {error}"
NET_BATCH_NOT_REREAD = ("{name}: the move went through, but its state could not be read "
                        "afterwards – log in to it again.")
NET_ORDER_UNCERTAIN = ("The topology is incomplete, so the order is only a proposal – go "
                       "through it. A device must not be updated before the ones that go "
                       "through it.")
NET_EMPTY = ("Nothing yet. “Search the network” listens to MNDP; a device can be updated "
             "only after logging in, which reads its state.")
NET_SERIOUS = "CAUTION: {detail}."
NET_SCANNING = "Searching…"
NET_SCAN_CANCELLED = "The search is ending…"
NET_SCAN_DONE = "{found} devices found."
NET_SCAN_GONE = "{count} from the previous search are gone: {names}."
NET_SCAN_FAILED = "The search failed: {error}"
NET_SOURCE_FAILED = "A source failed – {detail}"
NET_INVENTORY_SAVE_FAILED = "The inventory could not be saved: {error}"
NET_EXPORT_TITLE = "Inventory export"
NET_EXPORT_FILTER = "CSV (*.csv);;JSON (*.json)"
NET_EXPORT_FAILED = "The export failed: {error}"
NET_EXPORTED = "Wrote {count} devices to {path}"
