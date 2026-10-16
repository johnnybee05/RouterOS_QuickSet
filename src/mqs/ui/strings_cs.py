"""Czech UI texts (labels, buttons, help). Issue messages live in mqs.messages_cs."""

from __future__ import annotations

APP_TITLE = "MikroTik QuickSet Plus"

NAV = {
    "device": "Zařízení",
    "network": "Hromadný update",
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
DEVICE_KEY_PASSPHRASE = "Heslo SSH klíče"
DEVICE_KEY_PASSPHRASE_HINT = "jen u šifrovaného klíče"
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
    "po přejmenování může být starý, proto ho po přihlášení ověřím na zařízení. Stejnou adresu "
    "si stejným způsobem půjčím i při spojení přes link-local, pokud jinudy nepůjde přenést "
    "soubor – balíček RouterOS se přes konzoli MAC-Telnetu nevejde. Při odpojení adresu zase "
    "odeberu. Po aplikaci konfigurace se připojím znovu stejnou cestou přes MAC, i s dočasnou "
    "adresou.\n\nOvěřeno na RouterOS 7.20.7. Pokračovat?"
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
WAN_VLAN = "WAN je v VLAN (tagovaný provoz na portu)"
WAN_VLAN_ID = "VLAN ID"
WAN_VLAN_NAME = "Název VLAN rozhraní"
WAN_VLAN_HELP = (
    "Cetin dodává PPPoE v VLAN 848: zapněte a nechte 848. Na WAN portu vznikne rozhraní "
    "vlan848 a WAN (PPPoE, DHCP i statická adresa) pojede na něm — port sám zůstane bez "
    "IP adresy. Název nechte prázdný a pojmenuje se podle ID (vlan848)."
)
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
    "jen z ní. Se zapnutým IPv6 platí i pro IPv6 filtr (s management podsítí se přes IPv6 "
    "neotevře)."
)
FIREWALL_IPV6 = "Zapnout IPv6 (přebije disable-ipv6=yes z hardening bloku)"
FIREWALL_IPV6_HELP = (
    "Nastaví /ipv6 settings disable-ipv6=no a hardening blok ho už nevypne. Přidá IPv6 "
    "filtr i bez základního filtru. input: accept established/related/untracked, drop "
    "invalid, accept ICMPv6, loopback, DHCPv6 klient na WAN, accept z LAN, (správa z WAN), "
    "drop všeho, co nepřišlo z LAN. forward: established/related/untracked, drop invalid, "
    "accept ICMPv6, drop nových spojení z WAN. Ve v6 musí být balíček ipv6 povolený, jinak "
    "se nevygeneruje nic."
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
APPLY_UPDATE = "Aktualizovat RouterOS z adresáře Update (při resetu)"
APPLY_UPDATE_TIP = (
    "Před resetem se na zařízení nahraje balíček RouterOS pro jeho architekturu z adresáře "
    "Update a restart po resetu ho nainstaluje. Použije se nejnovější verze v adresáři, jen "
    "pokud je novější než ta na zařízení. Co se nainstaluje, ukážou kontroly před aplikací."
)
APPLY_UPDATE_DIR = "Adresář: {dir}"
APPLY_ROUTERBOOT = "Aktualizovat RouterBOOT (restart navíc)"
APPLY_ROUTERBOOT_TIP = (
    "Po ověření konfigurace, nebo po přechodu na jinou verzi RouterOS, se RouterBOOT "
    "aktualizuje na firmware, který nese RouterOS na zařízení, pokud je novější než ten, "
    "který běží (i bez aktualizace RouterOS). Zařízení se kvůli tomu ještě jednou restartuje."
)
APPLY_ROUTERBOOT_DOWN = "Snížit RouterBOOT, když je novější než RouterOS"
APPLY_ROUTERBOOT_DOWN_TIP = (
    "Zapíše firmware, který nese RouterOS na zařízení, i když je starší než RouterBOOT, "
    "který běží – to je stav po downgradu RouterOS. Zařízení to umí a je to vratné, ale "
    "potřeba to není: starší RouterOS běží i pod novějším RouterBOOTem (ověřeno 6.49.21 pod "
    "7.23.7). Samo se to nikdy nestane, downgrade RouterOS bootloader nesnižuje."
)
APPLY_VERSION = "Přejít na jinou verzi RouterOS (samostatně, bez resetu)"
APPLY_VERSION_TIP = (
    "Nainstaluje verzi z adresáře Update, kterou vyberete: novější restartem, starší "
    "příkazem /system package downgrade. Konfigurace zařízení zůstane, jak je – po "
    "přechodu ji nastavte znovu (Reset + aplikovat). Níž než na verzi, se kterou byla "
    "deska vyrobena, se jít nedá."
)
APPLY_VERSION_BUTTON = "Přejít na verzi…"
APPLY_VERSION_FLOOR = "Nejnižší možná verze: {floor}"
APPLY_VERSION_FLOOR_UNKNOWN = "Zařízení neuvádí, se kterou verzí bylo vyrobeno."
APPLY_VERSION_CURRENT = "{version} – verze na zařízení"
APPLY_VERSION_BLOCKED = "{version} – nelze"
APPLY_VERSION_NONE = "V adresáři Update není balíček pro toto zařízení"
APPLY_VERSION_DONE = "Zařízení má RouterOS {version}. Konfiguraci teď nastavte znovu."
APPLY_VERSION_FAILED = "Přechod na jinou verzi se nedokončil: {error}"
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
VERSION_TITLE = "Přechod na jinou verzi RouterOS"
VERSION_TEXT = (
    "Na zařízení {identity} se nainstaluje RouterOS {version} (teď {current}). "
    "Restartů: {reboots}. Po přechodu nastavení překontrolujte; zařízení se nemusí "
    "ohlásit na stejné adrese.\n\nPro potvrzení opište identity zařízení:"
)
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
CONNECT_KEY_PASSPHRASE = "Soukromý klíč je šifrovaný – vyplňte „Heslo SSH klíče“."
CONNECT_KEY_PASSPHRASE_WRONG = "Heslo SSH klíče je chybné – klíč se nepodařilo otevřít."
CONNECT_FAILED = "Nepodařilo se připojit žádným transportem:\n{details}"
CONNECT_CANCELLED = "Připojení bylo přerušeno."
CONNECT_UNSUPPORTED = "RouterOS {version} není podporovaný (podporováno 6.40+ a 7.x)."
CONNECT_FELL_BACK = "api-ssl není dostupné, připojeno přes SSH."
CONNECT_PROGRESS = "Připojuji a načítám informace o zařízení…"
SCAN_ERRORS = "Některá síťová rozhraní nelze poslouchat: {errors}"
APPLY_NOT_CONNECTED = "Pro aplikaci konfigurace se nejdřív připojte k zařízení."
APPLY_DONE = "Konfigurace aplikována."
APPLY_DONE_WITH_WARNINGS = "Konfigurace aplikována, ale některé kontroly selhaly."
APPLY_DONE_DISCONNECTED = "Konfigurace aplikována, zařízení odpojeno."
APPLY_CONFIRMED_DISCONNECTED = "Konfigurace potvrzena, zařízení odpojeno."
APPLY_SAVE_SCRIPT = "Uložit .rsc…"

# generic
ERROR_TITLE = "Chyba"
WARNING_TITLE = "Upozornění"
YES = "Ano"
NO = "Ne"

# --- stránka Hromadný update (hledání zařízení a dávkový update) ---------------------------------
NET_PAGE_TITLE = "Hromadný update"
NET_SCAN_GROUP = "Hledání zařízení v síti"
NET_SCAN_START = "Prohledat síť"
NET_SCAN_STOP = "Zastavit"
NET_SECONDS = "Poslouchat MNDP (s):"
NET_EXPORT = "Export tabulky…"
NET_SCAN_PORTS = "Skenovat porty"
NET_SCAN_PORTS_HINT = ("Zkusí porty 8729, 122, 22 a 8291 v zadaných sítích. Vypnuté, "
                       "protože se dotýká i zařízení, na která se nikdo neptal – a "
                       "otevřený port sám nic nedokazuje. Omezené na 50 adres/s.")
NET_SUBNETS = "Sítě:"
NET_SNMP = "SNMP community:"
NET_SNMP_HINT = ("Přečte sysObjectID přes SNMPv2c v zadaných sítích. Prázdné = SNMP se "
                 "nepoužije.")
NET_NEEDS_SUBNET = ("Skenování portů i SNMP potřebují aspoň jednu síť v poli „Sítě“ "
                    "(např. 192.168.55.0/24).")
NET_TABLE_GROUP = "Nalezená zařízení"
NET_LEGEND = ("Stav: ++ potvrzeno (přihlášeno, data ze zařízení – jen tyhle jde "
              "aktualizovat), + pravděpodobně MikroTik, ? odpovídá, ale nic na MikroTik "
              "neukazuje. Tučný řádek = od posledního scanu se změnil, podrobnost je "
              "v bublině. Vybrané řádky (Ctrl/Shift + klik) určují, ke kterým zařízením "
              "se přihlásí a které půjdou do dávky; bez výběru jde do dávky každé "
              "potvrzené.")
NET_STATE_CONFIRMED = "++"
NET_STATE_PROBABLE = "+"
NET_STATE_UNKNOWN = "?"
NET_ORDER_GROUP = "Dávkový update (od nejvzdálenějších zařízení k páteři)"
NET_NOT_CONFIRMED = "nepotvrzeno, do dávky nepůjde"
NET_ORDER_OK = "Pořadí sestavené z registračních a neighbor tabulek: {count} zařízení."
NET_LOGIN = "Přihlásit vybrané"
NET_LOGIN_TIP = ("Přihlásí se k vybraným zařízením a načte jejich stav, sousedy "
                 "a registrační tabulky. Teprve potom jde zařízení aktualizovat a dá se "
                 "sestavit pořadí. Ke všemu, co scan slyšel, se nepřihlašuje schválně – "
                 "na cizí síti by to bylo i cizí zařízení.")
NET_LOGIN_WITH = ("Přihlašovací údaje ze stránky Zařízení: {user} ({auth}), "
                  "API-SSL {api}, SSH {ssh}.")
NET_LOGIN_KEY = "klíč"
NET_LOGIN_PASSWORD = "heslo"
NET_LOGIN_NONE = "Na stránce Zařízení není vyplněný uživatel – není s čím se přihlásit."
NET_LOGGING_IN = "Přihlašuji se k {count} zařízení…"
NET_LOGIN_STEP = "Přihlašování: {name} – {state}"
NET_LOGIN_STATES = {"connecting": "připojuji", "confirmed": "načteno",
                    "failed": "nepřihlášeno"}
NET_LOGIN_DONE = "Načteno {ok} zařízení, nepřihlášeno {failed}."
NET_LOGIN_FAILED = "Přihlašování selhalo: {error}"
NET_LOGIN_REMEMBERED = "{name}: přihlášeno jako {user} (údaje uložené u zařízení)"
NET_REMEMBERED = ("Uložené údaje má {count} zařízení – zkouší se první, potom údaje "
                  "ze stránky Zařízení.")
NET_REMEMBERED_MEMORY = "Jen do zavření okna, na disk se neukládají."
NET_REMEMBERED_UNREADABLE = "Uložené přihlašovací údaje nejdou přečíst ({error})."
NET_REMEMBER_FAILED = "Přihlašovací údaje se nepodařilo uložit: {error}"
NET_FORGET = "Zapomenout uložené údaje"
NET_FORGET_TIP = ("Každé zařízení si pamatuje uživatele a heslo, se kterými se naposledy "
                  "přihlásilo, šifrované pod tímto účtem Windows. Tlačítko je smaže všechna.")
NET_FORGET_CONFIRM = "Smazat uložené přihlašovací údaje ({count} zařízení)?"
NET_NO_ADDRESS = "zařízení nemá IP adresu"
NET_TARGET = "Cílová verze:"
NET_TARGET_TIP = ("Verze RouterOS, na kterou mají zařízení přejít. Nabízí se verze, které "
                  "jsou v adresáři Update ({dir}).")
NET_HERE = "Toto PC je za:"
NET_HERE_TIP = ("Zařízení, přes které je tento počítač připojený do sítě. Půjde v dávce "
                "poslední bez ohledu na pořadí, protože jeho restart dávku ukončí.")
NET_HERE_UNKNOWN = "(nevím)"
NET_PARALLEL = "Najednou:"
NET_PARALLEL_TIP = ("Kolik zařízení se aktualizuje současně. Zařízení, za kterým něco "
                    "visí, jde vždy samo. Na páteři nechte 1.")
NET_ROUTERBOOT = "RouterBOOT"
NET_ROUTERBOOT_TIP = (
    "Po přechodu na cílovou verzi se RouterBOOT aktualizuje na firmware, který nese "
    "RouterOS na zařízení, pokud je novější než ten, který běží. Zařízení, které už cílovou "
    "verzi má a čeká na něm novější RouterBOOT, jde do dávky také – jen kvůli RouterBOOTu, "
    "jedním restartem a bez zásahu do konfigurace."
)
NET_ROUTERBOOT_DOWN = "Snížit RouterBOOT"
NET_WAIT_STATIONS = "Čekat na návrat stanic"
NET_WAIT_STATIONS_TIP = (
    "Po poslední verzi se u AP čeká (nejvýš 5 minut), až se vrátí stanice, které na něm "
    "visely před restartem. Stanice, které se nevrátí, dávku zastaví. Vypnuto: AP je "
    "hotové, jakmile mu naběhnou rádia, a dávka jde dál bez ohledu na stanice."
)
NET_BATCH_START = "Spustit dávku"
NET_BATCH_STOP = "Zastavit dávku"
NET_BATCH_STOP_TIP = ("Dávka se zastaví po zařízení, které se právě aktualizuje – "
                      "rozběhnutý přechod se nepřerušuje.")
NET_PLAN_DEVICE = "{number}. {name}  ({role}, teď {current})"
NET_PLAN_BEHIND = ", přes něj vede {count}"
NET_PLAN_FLOOR = "     nejnižší možná verze: {floor}"
NET_PLAN_FLOOR_UNKNOWN = "     nejnižší možnou verzi zařízení neuvádí"
NET_PLAN_HOP = "     → {version}: {files}"
NET_PLAN_DRIVERS = "     ovladač rádia: {drivers}"
NET_PLAN_CONFIRM = "     POZOR: downgrade nebo přechod mezi RouterOS 6 a 7"
NET_PLAN_UNCHANGED = "     už má {target}, nic se neinstaluje"
NET_PLAN_ROUTERBOOT = "     už má {target}, jen RouterBOOT {current} → {version}"
NET_PLAN_ROUTERBOOT_DOWN = ("     už má {target}, jen snížení RouterBOOT {current} → "
                            "{version}")
NET_PLAN_BLOCKED = "     NELZE: {detail}"
NET_PLAN_LIST = "{number}. {name}  ({summary})"
NET_ROLES = {"ap": "AP", "station": "stanice", "none": "bez rádia", "unknown": "?"}
NET_TREE = "Vazby:"
NET_TREE_LINK = "  {parent}{through} → {child}"
NET_TREE_ORPHAN = "  {name}: bez vazby na ostatní"
NET_BATCH_BLOCKED = ("Dávka se nespustí: {names} nemá použitelný plán. Odeberte je "
                     "z výběru, nebo doplňte balíčky do adresáře Update.")
NET_BATCH_NOTHING = "Všechna vybraná zařízení už mají {target}."
NET_BATCH_READY = "Do dávky jde {count} zařízení, restartů celkem {reboots}."
NET_BATCH_TITLE = "Dávkový update"
NET_BATCH_CONFIRM = ("Aktualizovat {count} zařízení na RouterOS {target}?\n\n"
                     "Pořadí: {names}\nRestartů celkem: {reboots}\n\n"
                     "Před každým přechodem se stáhne záloha konfigurace. Dávka se zastaví "
                     "na prvním zařízení, které se nevrátí nebo neprojde kontrolou.")
NET_BATCH_CONFIRM_FIRMWARE = ("Jen RouterBOOT (RouterOS už cílovou verzi má, jeden "
                              "restart): {names}")
NET_BATCH_WHY = "Dávku je potřeba potvrdit ručně: {reasons}"
NET_BATCH_WHY_ORDER = "pořadí není jisté (neúplná topologie)"
NET_BATCH_WHY_VERSION = "downgrade nebo přechod mezi RouterOS 6 a 7 ({names})"
NET_BATCH_TYPE = "Pro potvrzení napište cílovou verzi {target}:"
NET_BATCH_RUNNING = "Dávka běží…"
NET_BATCH_STOPPING = "Dávka se zastaví po aktuálním zařízení…"
NET_BATCH_STEP_RUNNING = "▶ {name}"
NET_BATCH_STEP_DONE = "OK {name}"
NET_BATCH_STEP_FAILED = "CHYBA {name}: {detail}"
NET_BATCH_DONE = "Dávka hotová: aktualizováno {count} zařízení."
NET_BATCH_CANCELLED = "Dávka zastavena: aktualizováno {done}, nezačato {rest}."
NET_BATCH_FAILED = "Dávka selhala: {error}"
NET_BATCH_NOT_REREAD = ("{name}: přechod proběhl, ale stav se po něm nepodařilo načíst – "
                        "přihlaste se k němu znovu.")
NET_ORDER_UNCERTAIN = ("Topologie je neúplná, takže pořadí je jen návrh – projděte ho. "
                       "Zařízení se nesmí aktualizovat dřív než ta, která přes něj vedou.")
NET_EMPTY = ("Zatím nic. Tlačítkem „Prohledat síť“ se poslechne MNDP; zařízení se dá "
             "aktualizovat teprve po přihlášení, které načte jeho stav.")
NET_SERIOUS = "POZOR: {detail}."
NET_SCANNING = "Hledám…"
NET_SCAN_CANCELLED = "Hledání se ukončuje…"
NET_SCAN_DONE = "Nalezeno {found} zařízení."
NET_SCAN_GONE = "Z předchozího hledání už není {count}: {names}."
NET_SCAN_FAILED = "Hledání selhalo: {error}"
NET_SOURCE_FAILED = "Některý zdroj selhal – {detail}"
NET_INVENTORY_SAVE_FAILED = "Inventář se nepodařilo uložit: {error}"
NET_EXPORT_TITLE = "Export inventáře"
NET_EXPORT_FILTER = "CSV (*.csv);;JSON (*.json)"
NET_EXPORT_FAILED = "Export se nepodařil: {error}"
NET_EXPORTED = "Zapsáno {count} zařízení do {path}"
