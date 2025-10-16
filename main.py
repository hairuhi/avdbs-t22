import os, re, time, pathlib
from io import BytesIO
from urllib.parse import urljoin, urlparse
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

# ===== Runtime =====
TIMEOUT = 25
TRACE_IMAGE_DEBUG = os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1"

# ===== Filters =====
EXCLUDE_IMAGE_SUBSTRINGS = [
    "/logo/", "/banner/", "/ads/", "/noimage", "/favicon", "/thumb/", "/placeholder/", "/loading", ".svg",
    "/img/level/", "mb3_", "avdbs_logo", "main-search", "new_9x9w.png", "/img/19cert/", "19_cert", "19_popup",
]

# Strict allowlist: only treat these as real content attachments
ALLOWED_IMG_DOMAINS = {"avdbs.com", "www.avdbs.com", "i1.avdbs.com"}
CONTENT_PATH_ALLOW_RE = re.compile(r"/(data|upload|board|files?|attach)/", re.I)

# ===== HTTP =====
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22Bot/6.2)",
    "Accept-Language": "ko,en;q=0.8",
    "Referer": AVDBS_BASE + "/",
    "Connection": "close",
})

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
        print("[warn] adult cookie not found → you may see placeholder images")

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
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}", data=data, files=files, timeout=60)
    print(f"[tg] {method} {r.status_code}")
    return r

def tg_send_text(text: str):
    return tg_post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,  # prevent OG preview cards
    })

def send_photo_file(bytes_data: bytes, caption: str | None):
    return tg_post("sendPhoto",
                   {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"},
                   files={"photo": ("image.jpg", BytesIO(bytes_data))})

# ---------- Parsing helpers ----------
def is_login_gate(resp, soup) -> bool:
    final_url = getattr(resp, "url", "") or ""
    title_txt = (soup.title.string.strip() if soup.title and soup.title.string else "")
    big_text = soup.get_text(" ", strip=True)[:2000]

    if "/login" in final_url:
        return True
    if "AVDBS" in title_txt and "로그인" in title_txt:
        return True
    if soup.find("input", {"name": "mb_id"}) and soup.find("input", {"name": "mb_password"}):
        return True
    if ("성인 인증" in big_text) and ("로그인" in big_text):
        return True
    return False

def pick_main_container(soup: BeautifulSoup):
    candidates = [
        "#bo_v_con", ".bo_v_con", "#view_content", ".viewContent",
        ".board_view", ".board-view", ".view-wrap", ".content-body",
        "article",
    ]
    for sel in candidates:
        node = soup.select_one(sel)
        if node:
            return node
    return soup

def summarize_text(node, max_chars=200) -> str:
    for t in node(["script", "style", "noscript"]): t.extract()
    txt = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
    return (txt[:max_chars] + "…") if len(txt) > max_chars else txt

# ---------- Core logic ----------
def parse_post(url: str):
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    if is_login_gate(resp, soup):
        print(f"[warn] login/adult gate detected, skip: {url}")
        return "(게이트)", "로그인/성인 인증 필요", []

    container = pick_main_container(soup)
    summary = summarize_text(container)
    title = soup.title.string.strip() if soup.title and soup.title.string else "(제목 없음)"

    images = []
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or img.get("data-echo")
        if not src:
            continue
        full = absolutize(url, src)
        if is_content_image(full):
            images.append(full)

    if not images:
        for a in container.find_all("a", href=True):
            h = a["href"].strip()
            if re.search(r"\.(jpg|jpeg|png|gif|webp)(?:\?|$)", h, re.I):
                full = absolutize(url, h)
                if is_content_image(full):
                    images.append(full)

    images = list(dict.fromkeys(images))

    if TRACE_IMAGE_DEBUG:
        print("[trace] images(after whitelist):", images[:10])
        try:
            tg_send_text("🔍 candidates:\n" + "\n".join(images[:10] or ["(no images)"]))
        except Exception:
            pass

    return title, summary, images

def process():
    r = SESSION.get(LIST_URL, timeout=TIMEOUT)
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    posts = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/board/" not in href:
            continue
        full = absolutize(LIST_URL, href)
        posts.append(full)
    if not posts:
        print("[info] no posts found")
        return

    latest = posts[0]
    title, summary, images = parse_post(latest)
    tg_send_text(f"📌 <b>{title}</b>\n{summary}\n{latest}")

    for img in images[:10]:
        data = download_bytes(img, latest)
        if data:
            send_photo_file(data, None)
            time.sleep(1)
        else:
            print(f"[warn] skip image due to download failure: {img}")

if __name__ == "__main__":
    process()
