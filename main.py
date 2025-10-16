import os, re, time, pathlib
from io import BytesIO
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
import requests
from bs4 import BeautifulSoup

# ===== Telegram =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ===== Target =====
AVDBS_BASE = os.getenv("AVDBS_BASE", "https://www.avdbs.com").rstrip("/")
AVDBS_BOARD_PATH = os.getenv("AVDBS_BOARD_PATH", "/board/t22")
LIST_URL = f"{AVDBS_BASE}{AVDBS_BOARD_PATH}"

# ===== Auth =====
AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()

# ===== Runtime / State =====
TIMEOUT = 25
TRACE_IMAGE_DEBUG = os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1"
FORCE_SEND_LATEST = os.getenv("FORCE_SEND_LATEST", "0").strip() == "1"
RESET_SEEN = os.getenv("RESET_SEEN", "0").strip() == "1"
SEEN_FILE = os.getenv("SEEN_SET_FILE", "state/avdbs_t22_seen.txt")

# ===== Filters =====
EXCLUDE_IMAGE_SUBSTRINGS = [
    "/logo/", "/banner/", "/ads/", "/noimage", "/favicon", "/thumb/", "/placeholder/", "/loading", ".svg",
    "/img/level/", "mb3_", "avdbs_logo", "main-search", "new_9x9w.png", "/img/19cert/", "19_cert", "19_popup",
]
ALLOWED_IMG_DOMAINS = {"avdbs.com", "www.avdbs.com", "i1.avdbs.com"}
CONTENT_PATH_ALLOW_RE = re.compile(r"/(data|upload|board|files?|attach)/", re.I)

# ===== HTTP =====
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22Bot/7.0)",
    "Accept-Language": "ko,en;q=0.8",
    "Referer": AVDBS_BASE + "/",
    "Connection": "close",
})

def ensure_state_dir():
    pathlib.Path("state").mkdir(parents=True, exist_ok=True)

def load_seen() -> set[str]:
    ensure_state_dir()
    if RESET_SEEN:
        print("[debug] RESET_SEEN=1 → fresh run, ignoring previous seen set")
        return set()
    p = pathlib.Path(SEEN_FILE)
    if not p.exists():
        return set()
    with open(p, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def append_seen(keys: list[str]):
    if not keys:
        return
    ensure_state_dir()
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        for k in keys:
            f.write(k + "\n")

# ===== Cookies (site + CDN) =====
def cookie_string_to_jar(raw: str) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    base_host = urlparse(AVDBS_BASE).hostname or "www.avdbs.com"
    cdn_host  = "i1.avdbs.com"
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        for dom in (base_host, "." + base_host.lstrip("."), cdn_host, "." + cdn_host):
            jar.set(k, v, domain=dom)
    return jar

if AVDBS_COOKIE:
    SESSION.cookies.update(cookie_string_to_jar(AVDBS_COOKIE))
    ck = AVDBS_COOKIE.lower()
    if "adult_chk=1" not in ck and "adult=ok" not in ck:
        print("[warn] adult cookie not found → placeholder/login images may appear")

# ===== Helpers =====
def absolutize(base_url: str, url: str) -> str:
    if not url: return ""
    if url.startswith("//"): return "https:" + url
    return urljoin(base_url, url)

def is_excluded_image(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in EXCLUDE_IMAGE_SUBSTRINGS)

def is_content_image(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host not in ALLOWED_IMG_DOMAINS:
            return False
        if not CONTENT_PATH_ALLOW_RE.search(u.path or ""):
            return False
    except Exception:
        return False
    if is_excluded_image(url):
        return False
    return True

def canon_url_remove_noise(u: str) -> str:
    """
    같은 글인데 reply, sort 같은 쿼리로 변형되는 걸 방지 (중복 키 안정화)
    """
    pr = urlparse(u)
    if not pr.query:
        return u
    kept = []
    for k, v in parse_qsl(pr.query, keep_blank_values=True):
        if k.lower() in {"reply", "sort", "page", "s", "g"}:
            continue
        kept.append((k, v))
    new_q = urlencode(kept, doseq=True)
    return urlunparse((pr.scheme, pr.netloc, pr.path, pr.params, new_q, pr.fragment))

def download_bytes(url: str, referer: str) -> bytes | None:
    try:
        headers = {"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
        r = SESSION.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content
        print(f"[warn] download {r.status_code}: {url}")
    except Exception as e:
        print(f"[warn] download failed: {url} err={e}")
    return None

def tg_post(method: str, data: dict, files=None):
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}", data=data, files=