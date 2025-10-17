import os, re, time, pathlib
from io import BytesIO
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
import requests
from bs4 import BeautifulSoup

# ========== Telegram ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ========== Target ==========
AVDBS_BASE = os.getenv("AVDBS_BASE", "https://www.avdbs.com").rstrip("/")
AVDBS_BOARD_PATH = os.getenv("AVDBS_BOARD_PATH", "/board/t22")
LIST_URL = f"{AVDBS_BASE}{AVDBS_BOARD_PATH}"

# ========== Auth ==========
AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()

# ========== Runtime / State ==========
TIMEOUT = 25
TRACE_IMAGE_DEBUG = os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1"
FORCE_SEND_LATEST = os.getenv("FORCE_SEND_LATEST", "0").strip() == "1"
RESET_SEEN = os.getenv("RESET_SEEN", "0").strip() == "1"
SEEN_FILE = os.getenv("SEEN_SET_FILE", "state/avdbs_t22_seen.txt")

# ========== Filters ==========
EXCLUDE_IMAGE_SUBSTRINGS = [
    "/logo/", "/banner/", "/ads/", "/noimage", "/favicon", "/thumb/",
    "/placeholder/", "/loading", ".svg",
    "/img/level/", "mb3_", "avdbs_logo", "main-search", "new_9x9w.png",
    "/img/19cert/", "19_cert", "19_popup",
]
ALLOWED_IMG_DOMAINS = {"avdbs.com", "www.avdbs.com", "i1.avdbs.com"}
CONTENT_PATH_ALLOW_RE = re.compile(r"/(data|upload|board|files?|attach)/", re.I)

# ========== HTTP ==========
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22Bot/7.6)",
    "Accept-Language": "ko,en;q=0.8",
    "Referer": AVDBS_BASE + "/",
    "Connection": "close",
})

# ========== State helpers ==========
def ensure_state_dir():
    pathlib.Path("state").mkdir(parents=True, exist_ok=True)

def load_seen() -> set[str]:
    ensure_state_dir()
    if RESET_SEEN:
        print("[debug] RESET_SEEN=1 → fresh run, ignoring previous state")
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

# ========== Cookies (site + CDN) ==========
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

# ========== URL canonicalization ==========
def canon_url_remove_noise(u: str) -> str:
    """reply/sort/page 같은 쿼리를 제거해 중복키를 안정화."""
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

# ========== URL filters: only article links ==========
ARTICLE_URL_RE = re.compile(r"^/board/\d+(?:\?.*)?$", re.I)  # /board/숫자 → 글
BOARD_TAB_RE   = re.compile(r"^/board/t\d+(?:/|$)", re.I)    # /board/t90 등 → 탭(제외)

def is_article_url(href: str, base: str) -> str | None:
    """글 링크이면 절대URL 반환, 아니면 None"""
    if not href:
        return None
    full = urljoin(base, href.strip())
    u = urlparse(full)
    if u.netloc not in {"www.avdbs.com", "avdbs.com"}:
        return None
    if BOARD_TAB_RE.match(u.path):          # 탭 제외
        return None
    if ARTICLE_URL_RE.match(u.path):        # 글만 통과
        return full
    return None

# ========== Common helpers ==========
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
    try:
        ok = r.json().get("ok", None)
    except Exception:
        ok = None
    print(f"[tg] {method} {r.status_code} ok={ok}")
    return r

def tg_send_text(text: str):
    return tg_post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,  # 링크 카드 꺼짐
    })

def send_photo_file(bytes_data: bytes, caption: str | None):
    return tg_post("sendPhoto",
                   {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"},
                   files={"photo": ("image.jpg", BytesIO(bytes_data))})

# ========== Gate detection & container ==========
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
    cands = [
        "#bo_v_con", ".bo_v_con", "#view_content", ".viewContent",
        ".board_view", ".board-view", ".view-wrap", ".content-body",
        "article",
    ]
    for sel in cands:
        node = soup.select_one(sel)
        if node:
            return node
    return soup

def summarize_text(node, max_chars=200) -> str:
    for t in node(["script", "style", "noscript"]): t.extract()
    txt = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
    return (txt[:max_chars] + "…") if len(txt) > max_chars else txt

# ========== Parsing ==========
def parse_list() -> list[dict]:
    """t22 목록에서 '게시글' 링크만 추출 (/board/숫자…)."""
    r = SESSION.get(LIST_URL, timeout=TIMEOUT)
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    posts = {}
    for a in soup.find_all("a", href=True):
        art = is_article_url(a["href"], LIST_URL)
        if not art:
            continue
        title = a.get_text(strip=True) or "(제목 없음)"
        art = canon_url_remove_noise(art)
        if art not in posts or len(title) > len(posts[art]["title"]):
            posts[art] = {"url": art, "title": title}

    res = list(posts.values())
    res.sort(key=lambda x: x["url"], reverse=True)
    print(f"[debug] list collected (articles only): {len(res)} items")
    return res

def parse_post(url: str):
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    if is_login_gate(resp, soup):
        print(f"[warn] login/adult gate detected, skip: {url}")
        return None  # 게이트는 완전 스킵(캡션도 보내지 않음)

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

    images = list(dict.fromkeys(images))  # dedup

    if TRACE_IMAGE_DEBUG:
        print("[trace] images(after whitelist):", images[:10])
        try:
            tg_send_text("🔍 candidates:\n" + "\n".join(images[:10] or ["(no images)"]))
        except Exception:
            pass

    return {"title": title, "summary": summary, "images": images}

# ========== Main ==========
def process():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID required")

    posts = parse_list()
    if not posts:
        print("[info] no posts found")
        return

    seen = load_seen()
    to_send = []
    for p in posts:
        key = f"avdbs:t22:{p['url']}"
        if key not in seen:
            d = dict(p)
            d["_seen_key"] = key
            to_send.append(d)

    if FORCE_SEND_LATEST and not to_send and posts:
        latest = dict(posts[0])
        latest["_seen_key"] = f"avdbs:t22:{latest['url']}"
        to_send = [latest]
        print("[debug] FORCE_SEND_LATEST=1 → sending most recent once")

    if not to_send:
        print("[info] no new posts to send")
        return

    sent_keys = []
    for p in to_send:
        url = p["url"]
        data = parse_post(url)
        if data is None:  # gate
            continue

        title = data["title"]
        summary = data["summary"]
        images = data["images"]

        tg_send_text(f"📌 <b>{title}</b>\n{summary}\n{url}")
        time.sleep(1)

        for img in images[:10]:
            blob = download_bytes(img, url)
            if blob:
                send_photo_file(blob, None)
                time.sleep(1)
            else:
                print(f"[warn] skip image due to download failure: {img}")

        sent_keys.append(p["_seen_key"])

    append_seen(sent_keys)
    print(f"[info] appended {len(sent_keys)} keys")

if __name__ == "__main__":
    process()
