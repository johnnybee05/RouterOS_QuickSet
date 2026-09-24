"""Czech UI texts (labels, buttons, help). Issue messages live in mqs.messages_cs."""

from __future__ import annotations

APP_TITLE = "MikroTik QuickSet Plus"

NAV = {
    "device": "Zařízení",
    "basic": "Základ",
    "lan": "LAN / Bridge",
    "firewall": "Firewall",
    "services": "Služby a hardening",
    "preview": "Náhled",
    "apply": "Aplikovat",
}

# status bar / header
DISCONNECTED = "Odpojeno"
CONNECTED = "Připojeno"
OFFLINE_DIALECT = "Offline – cíl RouterOS {version}"
DIALECT_BADGE = "RouterOS v{major} ({version})"
SECURITY = {
    "anonymous-dh": "api-ssl: nešifrovaná identita protistrany",
    "verified": "api-ssl: ověřený certifikát",
    "ssh-host-key": "SSH: otisk klíče {fingerprint}",
    "unknown": "",
}

# device page
DEVICE_CONNECT_GROUP = "Připojení"
DEVICE_HOST = "IP adresa / hostname"
DEVICE_USER = "Uživatel"
DEVICE_PASSWORD = "Heslo"
DEVICE_KEY = "SSH klíč (volitelně)"
DEVICE_TRANSPORT = "Transport"
TRANSPORTS = {"auto": "API-SSL, při nedostupnosti SSH", "api-ssl": "Jen API-SSL (8729)",
              "ssh": "Jen SSH"}
DEVICE_SSH_PORT = "SSH port"
DEVICE_API_PORT = "API-SSL port"
DEVICE_CA = "CA certifikát (volitelně)"
DEVICE_CONNECT = "Připojit"
DEVICE_DISCONNECT = "Odpojit"
DEVICE_CANCEL = "Přerušit"
DEVICE_SCAN_GROUP = "Zařízení v síti (MNDP)"
DEVICE_SCAN_START = "Vyhledat v síti"
DEVICE_SCAN_STOP = "Zastavit"
DEVICE_SCAN_COLUMNS = ["Identity", "IP adresa", "MAC adresa", "Verze", "Model", "Síťová karta",
                       "Rozhraní zařízení"]
DEVICE_SCAN_HINT = ("Dvojklikem zařízení převezmete do formuláře. Windows Firewall může "
                    "blokovat příchozí UDP 5678.")
DEVICE_USE_MAC = "Připojit přes MAC"
DEVICE_MAC_HINT = ("Vyberte zařízení v tabulce. Zkusí se IPv6 link-local, jinak MAC-Telnet: "
                   "na zařízení se přidá dočasná IP a připojí se přes ni. Neověřeno na "
                   "zařízení.")
MAC_CONFIRM_TITLE = "Připojit přes MAC adresu"
MAC_CONFIRM_TEXT = (
    "Zařízení {identity} (MAC {mac}) nemá použitelnou IP. Zkusí se IPv6 link-local; pokud "
    "neodpoví, přihlásím se přes MAC-Telnet a na rozhraní {interface} přidám dočasnou adresu "
    "{address} (komentář mqs-temp), přes kterou se pak připojím. Název rozhraní hlásí MNDP a "
    "po přejmenování může být starý, proto ho po přihlášení ověřím na zařízení. Při odpojení "
    "adresu zase odeberu. Po aplikaci konfigurace se připojím znovu stejnou cestou přes MAC, i "
    "s dočasnou adresou.\n\nOvěřeno na RouterOS 7.20.7. Pokračovat?"
)
CONNECTED_BY_MAC = "Připojeno: {mac} (přes {method}; {transport} na {host})"
MAC_TEMP_ADDRESS = ("dočasná adresa na zařízení: {address} (komentář mqs-temp; při odpojení ji "
                    "odeberu, reset ji smaže sám)")
MAC_RETURNING = "Odpojuji – odebírám dočasnou adresu {address}…"
MAC_RETURNED = "Odpojeno – dočasná adresa {address} odebrána"
MAC_NOT_RETURNED = ("Odpojeno, ale dočasná adresa {address} na zařízení zůstala. Odeberte ji "
                    "ručně (IP → Addresses, komentář mqs-temp).")
MAC_NO_INTERFACE = ("Nepodařilo se zjistit, přes kterou síťovou kartu je zařízení dostupné. "
                    "Spusťte vyhledávání znovu.")
MAC_NO_ADDRESS = "V síti této karty není volná adresa, kterou by šlo dočasně přidělit."
DEVICE_INFO_GROUP = "Detekce"
DEVICE_NOTES = "Poznámky k modelu (z dokumentace MikroTiku, neověřeno na zařízení):"
DEVICE_PORTS_GROUP = "Porty"
DEVICE_PORT_COLUMNS = ["Port", "default-name", "Typ", "Switch čip", "Stav", "MAC"]
DEVICE_OFFLINE_GROUP = "Offline (bez zařízení)"
DEVICE_OFFLINE_VERSION = "Cílová verze RouterOS"
DEVICE_OFFLINE_PORTS = "Porty (oddělené čárkou)"
DEVICE_OFFLINE_IPV6 = "Balíček ipv6 (v6)"
IPV6_STATES = {"unknown": "neznámý", "enabled": "nainstalovaný a povolený",
               "disabled": "zakázaný", "missing": "chybí"}
INFO_FIELDS = {
    "version": "Verze", "board": "Model (board)", "architecture": "Architektura",
    "uptime": "Uptime", "memory": "Volná paměť", "identity": "Identity",
    "routerboard": "RouterBOARD / firmware", "wireless": "Wi-Fi", "ipv6": "Balíček ipv6",
}

# basic page
BASIC_IDENTITY = "Identity zařízení"
BASIC_MODE = "Režim (předvyplní formulář)"
MODES = {"router": "Router", "bridge-ap": "Bridge / AP", "custom": "Vlastní"}
MODE_APPLY = "Předvyplnit"
PORT_NAMES_GROUP = "Názvy portů"
PORT_NAMES_COLUMNS = ["Port (default-name)", "Název", "Teď na zařízení"]
PORT_NAMES_HELP = (
    "Název portu upravíte dvojklikem. Skript porty přejmenuje jako první "
    "(/interface ethernet set [ find default-name=… ] name=…), takže název platí i po resetu. "
    "Názvy z připojeného zařízení se do profilu převezmou samy."
)
PORT_NAME_EMPTY = "Název portu nesmí být prázdný."
PORT_NAME_TAKEN = "Název {name} už má port {port}."
WAN_GROUP = "WAN"
WAN_ENABLED = "Použít WAN"
WAN_INTERFACE = "Rozhraní"
WAN_TYPE = "Typ"
WAN_TYPES = {"dhcp": "DHCP client", "static": "Statická IP", "pppoe": "PPPoE"}
WAN_ADDRESS = "Adresa / maska (CIDR)"
WAN_GATEWAY = "Brána"
WAN_DNS = "DNS (volitelně, čárkou)"
WAN_PPPOE_USER = "Uživatel PPPoE"
WAN_PPPOE_PASSWORD = "Heslo PPPoE"
WAN_PPPOE_NAME = "Název rozhraní"
WAN_LIST = "Přidat do interface listu WAN"
WAN_MSS = "MSS clamping"
WAN_NAT = "NAT masquerade na WAN"

# LAN / bridge page
BRIDGE_GROUP = "Bridge"
BRIDGE_NAME = "Název bridge"
BRIDGE_PROTOCOL = "protocol-mode"
BRIDGE_PRIORITY = "Priority (volitelně)"
BRIDGE_AGEING = "ageing-time [s] (volitelně)"
BRIDGE_VLAN = "vlan-filtering"
BRIDGE_PVID = "PVID"
BRIDGE_FRAME_TYPES = "frame-types"
BRIDGE_IP_FIREWALL = "use-ip-firewall (vypíná HW offload)"
BRIDGE_PORTS = "Členské porty (WAN port je automaticky vyloučen)"
OFFLOAD_GROUP = "Hardware offload bridge"
OFFLOAD_MODES = {"auto": "Auto (dle detekce)", "force-on": "Vynutit zapnuto", "off": "Vypnuto"}
OFFLOAD_COLUMNS = ["Port", "Switch čip", "Offload", "Důvod"]
OFFLOAD_STATUS = {"yes": "ANO", "no": "NE", "uncertain": "nejisté", "not-offered": "—"}
OFFLOAD_HELP = (
    "HW offload přepíná provoz mezi porty přímo ve switch čipu. Porty na různých čipech se "
    "mezi sebou neoffloadují – provoz mezi skupinami jde přes CPU. Nezávisí na FastTracku."
)
OFFLOAD_SWITCH_TO_RSTP = "Přepnout na rstp"
LAN_GROUP = "IP adresa LAN"
LAN_ADDRESSING = "Adresace bridge"
LAN_ADDRESSINGS = {"static": "Statická", "dhcp-client": "DHCP client", "none": "Žádná"}
LAN_ADDRESS = "Adresa / maska (CIDR)"
LAN_GATEWAY = "Brána (výchozí trasa, volitelně)"
LAN_HELP = (
    "V režimu Bridge / AP zvolte Statická, pokud má mít zařízení pevnou adresu pro správu. "
    "Brána vytvoří výchozí trasu (/ip route add dst-address=0.0.0.0/0), aby zařízení dosáhlo "
    "na DNS, NTP a SNMP mimo svoji síť. Pokud je zapnutá WAN, výchozí trasu přidává už ona."
)
DHCP_GROUP = "DHCP server"
DHCP_ENABLED = "Zapnout DHCP server"
DHCP_POOL_START = "Pool od"
DHCP_POOL_END = "Pool do"
DHCP_GATEWAY = "Brána (prázdné = adresa bridge)"
DHCP_DNS = "DNS (prázdné = automaticky)"
DHCP_DNS_HELP = (
    "Prázdné: s povolenými vzdálenými DNS dotazy adresa bridge, jinak DNS servery ze Služeb "
    "a hardeningu."
)
DHCP_LEASE = "Lease time [s]"
DHCP_NAME = "Název serveru"
DHCP_COMMENT = "Název sítě (komentář)"

# firewall page
FIREWALL_BASIC = "Nasadit základní filtr"
FIREWALL_BASIC_HELP = (
    "input: accept established/related/untracked, drop invalid, accept ICMP, accept z LAN, "
    "(správa z WAN), drop všeho, co nepřišlo z LAN. forward: established/related/untracked, "
    "drop invalid, drop nových spojení z WAN, která nejsou dst-nat. Pravidla mají komentář "
    "„quickset: …“; při aplikaci na běžící konfiguraci se ta z dřívějšího běhu nahradí a "
    "filtr se zařadí nad pravidla, která už na zařízení jsou."
)
FIREWALL_WAN_MANAGEMENT = "Povolit správu z WAN (api-ssl, SSH, Winbox, SNMP, discovery)"
FIREWALL_WAN_MANAGEMENT_HELP = (
    "Otevře z WAN porty služeb tak, jak je nastaví hardening blok (např. SSH 122). "
    "Vypnuté služby se neotevírají. Je-li zadaná management podsíť, pravidla přijímají "
    "jen z ní."
)
FASTTRACK_GROUP = "FastTrack"
FASTTRACK_ENABLED = "Zapnout FastTrack"
FASTTRACK_HW = "hw-offload na FastTrack pravidle (jen pokud ho zařízení podporuje)"
FASTTRACK_HELP = (
    "FastTrack obchází většinu zpracování paketů v CPU, a tím zrychluje routing.\n"
    "• Simple queues a HTB nad fasttrackovaným provozem nevidí traffic.\n"
    "• Účtování provozu a statistiky per-connection jsou zkreslené.\n"
    "• Mangle pravidla se na fasttrackované pakety neuplatní. MSS clamping na SYN paketech "
    "funguje dál, protože ty se nefasttrackují.\n"
    "• S PPPoE WAN funguje, ale bez hardwarové akcelerace."
)
FASTTRACK_SHAPING_QUESTION = (
    "Používáte na zařízení tvarování provozu (simple queues nebo queue tree / HTB)?\n\n"
    "FastTrack by ho obešel."
)
FASTTRACK_SHAPING_TITLE = "FastTrack a tvarování provozu"

# services page
SERVICES_FIELDS_GROUP = "Nejčastěji měněné hodnoty"
SERVICES_DNS = "DNS servery"
SERVICES_DNS_REMOTE = "Povolit vzdálené DNS dotazy (allow-remote-requests)"
SERVICES_DNS_REMOTE_HELP = (
    "Zapnuto: router odpovídá na DNS dotazy z LAN a DHCP server přiděluje jako DNS adresu "
    "bridge. Vypnuto: DHCP server přiděluje DNS servery výše. Zapnout jde jen se základním "
    "filtrem firewallu."
)
SERVICES_SSH_PORT = "SSH port"
SERVICES_SNMP = "SNMP community"
SERVICES_NTP = "NTP servery"
SERVICES_TZ = "Časové pásmo"
SERVICES_EDITOR_GROUP = "Hardening blok (RouterOS v7 syntaxe, pro v6 se přeloží)"
SERVICES_RESET = "Obnovit výchozí"
SERVICES_RESET_CONFIRM = "Přepsat hardening blok výchozí firemní šablonou?"
SERVICES_ROBUST_LOGGING = "Robustní varianta logování (find podle topics místo indexů)"
SERVICES_EXTENSIONS_GROUP = "Volitelná rozšíření"
SERVICES_CERTIFICATE = "Přiřadit certifikát k api-ssl"
SERVICES_MGMT_SUBNET = "Omezit ssh, winbox a api-ssl na subnet"
ACCESS_OK = "Přístupové cesty po aplikaci: {paths}"
ACCESS_BLOCKED = "Po aplikaci nezůstane žádná přístupová cesta – aplikace je zablokovaná."

# preview page
PREVIEW_V6 = "v6"
PREVIEW_V7 = "v7"
PREVIEW_BOTH = "vedle sebe"
PREVIEW_COPY = "Kopírovat"
PREVIEW_SAVE = "Uložit .rsc"
PREVIEW_COPIED = "Skript zkopírován do schránky."
PREVIEW_SAVE_FILTER = "RouterOS skript (*.rsc)"
PREVIEW_ISSUES = "Upozornění"

# apply page
APPLY_MODE_GROUP = "Režim"
APPLY_MODES = {
    "generate": "Jen vygenerovat (bez sítě)",
    "import": "Aplikovat na běžící konfiguraci (/import)",
    "reset": "Reset + aplikovat (run-after-reset)",
}
APPLY_ROLLBACK = "Rollback pojistka: obnovit zálohu po [min], pokud konfiguraci nepotvrdím"
APPLY_DOWNLOAD = "Stáhnout zálohu a export do složky"
APPLY_KEEP_BACKUPS = "Ponechat na zařízení posledních záloh"
APPLY_KEEP_BACKUPS_ALL = "všechny"
APPLY_RESET_DELAY = "Prodleva po startu [s]"
APPLY_KEEP_USERS = "Zachovat uživatele (keep-users)"
APPLY_KEEP_USERS_TIP = (
    "Reset ponechá uživatele a jejich hesla. Aplikace se po resetu připojí pod stejným "
    "účtem a heslo admina změní, jen pokud ho zadáte."
)
APPLY_ADMIN_PASSWORD = "Nové heslo uživatele admin (po resetu)"
APPLY_ADMIN_PASSWORD_KEEP = "nepovinné – prázdné ponechá současné"
APPLY_PREFLIGHT = "Kontroly před aplikací"
APPLY_DRY_RUN = "Dry-run a aplikovat…"
APPLY_BUTTON = "Aplikovat"
APPLY_CONFIRM_WORKS = "Potvrdit, že konfigurace funguje"
APPLY_CHECKLIST = "Výsledek"
APPLY_LOG = "Průběh"
APPLY_CHECK_OK = "OK"
APPLY_CHECK_FAIL = "CHYBA"
DRY_RUN_TITLE = "Dry-run – přesně toto se pošle na zařízení"
DRY_RUN_CONFIRM = "Rozumím, aplikovat"
DIALECT_MISMATCH_CONFIRM = "Skript pro jinou hlavní verzi RouterOS aplikovat přesto"
RESET_TITLE = "Reset konfigurace zařízení"
RESET_TEXT = (
    "Zařízení {identity} se resetuje bez výchozí konfigurace, restartuje a po startu "
    "naimportuje nahraný skript. {users}\n\nPro potvrzení opište identity zařízení:"
)
RESET_USERS_DELETED = "Všichni uživatelé kromě admina budou smazáni."
RESET_USERS_KEPT = "Uživatelé a jejich hesla zůstanou zachováni."

# profiles
MENU_PROFILE = "Profil"
PROFILE_NEW = "Nový"
PROFILE_SAVE = "Uložit"
PROFILE_SAVE_AS = "Uložit jako…"
PROFILE_OPEN = "Otevřít…"
PROFILE_IMPORT = "Importovat ze souboru…"
PROFILE_EXPORT = "Exportovat do souboru…"
PROFILE_NAME_PROMPT = "Název profilu:"
PROFILE_FILTER = "Profil QuickSet Plus (*.json)"

# input syntax (checked in the UI before values reach the model)
INVALID_CIDR = "Zadejte adresu ve tvaru a.b.c.d/nn"
INVALID_IP = "Zadejte IPv4 adresu a.b.c.d"
INVALID_IP_LIST = "Zadejte IPv4 adresy oddělené čárkou"
INVALID_NETWORK = "Zadejte síť ve tvaru a.b.c.d/nn"
INVALID_NUMBER = "Zadejte celé číslo {minimum}–{maximum}"
INVALID_RANGE = "Začátek poolu musí být menší nebo roven konci"
WAN_PORT_LOCKED = "WAN port"

# connection errors / session
CONNECT_AUTH_FAILED = "Přihlášení selhalo – zkontrolujte uživatele a heslo."
CONNECT_FAILED = "Nepodařilo se připojit žádným transportem:\n{details}"
CONNECT_CANCELLED = "Připojení bylo přerušeno."
CONNECT_UNSUPPORTED = "RouterOS {version} není podporovaný (podporováno 6.40+ a 7.x)."
CONNECT_FELL_BACK = "api-ssl není dostupné, připojeno přes SSH."
CONNECT_PROGRESS = "Připojuji a načítám informace o zařízení…"
SCAN_ERRORS = "Některá síťová rozhraní nelze poslouchat: {errors}"
APPLY_NOT_CONNECTED = "Pro aplikaci konfigurace se nejdřív připojte k zařízení."
APPLY_DONE = "Konfigurace aplikována."
APPLY_DONE_WITH_WARNINGS = "Konfigurace aplikována, ale některé kontroly selhaly."
APPLY_SAVE_SCRIPT = "Uložit .rsc…"

# generic
ERROR_TITLE = "Chyba"
WARNING_TITLE = "Upozornění"
YES = "Ano"
NO = "Ne"
