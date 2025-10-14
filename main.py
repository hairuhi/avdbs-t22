import os
import re
import time
import pathlib
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

AVDBS_BASE = os.getenv("AVDBS_BASE", "https://www.avdbs.com").rstrip("/")
AVDBS_BOARD_PATH = os.getenv("AVDBS_BOARD_PATH", "/board/t22")
LIST_URL   = f"{AVDBS_BASE}{AVDBS_BOARD_PATH}"

AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()
AVDBS_ID = os.getenv("AVDBS_ID", "").strip()
AVDBS_PW = os.getenv("AVDBS_PW", "").strip()

SEEN_FILE = os.getenv("SEEN_SET_FILE", "state/avdbs_t22_seen.txt")
ENABLE_HEARTBEAT = os.getenv("ENABLE_HEARTBEAT", "0").strip() == "1"
HEARTBEAT_TEXT = os.getenv("HEARTBEAT_TEXT", "🧪 AVDBS t22 bot alive.")
FORCE_SEND_LATEST = os.getenv("FORCE_SEND_LATEST", "0").strip() == "1"
RESET_SEEN = os.getenv("RESET_SEEN", "0").strip() == "1"

DOWNLOAD_AND_UPLOAD = os.getenv("DOWNLOAD_AND_UPLOAD", "1").strip() == "1"
TRACE_IMAGE_DEBUG   = os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1"

EXCLUDE_IMAGE_SUBSTRINGS = [
    "/logo/",
    "/banner/",
    "/ads/",
    "/noimage",
    "/favicon",
    "/thumb/",
    "/placeholder/",
    "/loading",
    ".svg",
    "/img/level/",
    "mb3_",
    "avdbs_logo",
    "main-search",
    "new_9x9w.png",
    "/img/19cert/",
    "19_cert",
    "19_popup",
]
_extra = os.getenv("EXCLUDE_IMAGE_SUBSTRINGS", "").strip()
if _extra:
    EXCLUDE_IMAGE_SUBSTRINGS += [s.strip() for s in _extra.split(",") if s.strip()]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22Bot/6.0)",
        "Accept-Language": "ko,ko-KR;q=0.9,en;q=0.8",
        "Referer": AVDBS_BASE + "/",
        "Connection": "close",
    }
)
TIMEOUT = 25

def ensure_state_dir():
    pathlib.Path("state").mkdir(parents=True, exist_ok=True)


def load_seen() -> set:
    ensure_state_dir()
    if RESET_SEEN:
        print("[debug] RESET_SEEN=1 → ignoring previous seen set this run")
        return set()
    s = set()
    p = pathlib.Path(SEEN_FILE)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            s = {line.strip() for line in f if line.strip()}
    return s


def append_seen(keys: list[str]):
    if not keys:
        return
    ensure_state_dir()
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        for k in keys:
            f.write(k + "\n")


def get_encoding_safe_text(resp: requests.Response) -> str:
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ansi_x3.4-1968"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def absolutize(base_url: str, url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base_url, url)


def is_excluded_image(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in EXCLUDE_IMAGE_SUBSTRINGS)

def cookie_string_to_jar(raw: str) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        jar.set(k.strip(), v.strip(), domain=urlparse(AVDBS_BASE).hostname)
    return jar


def ensure_auth():
    if AVDBS_COOKIE:
        SESSION.cookies.update(cookie_string_to_jar(AVDBS_COOKIE))
        print("[auth] Using AVDBS_COOKIE (browser-exported cookies).")
        ck = AVDBS_COOKIE.lower()
        if "adult_chk=1" not in ck and "adult=ok" not in ck:
            print("[warn] adult cookie not found in AVDBS_COOKIE (adult_chk=1 or adult=ok). You may see adult-cert placeholders.")
        return
    if AVDBS_ID and AVDBS_PW:
        print("[auth] AVDBS_ID/PW provided, but cookie-less login is site-specific; please prefer AVDBS_COOKIE.")
    else:
        print("[auth] No cookie provided. You may see 'login required' pages. Set AVDBS_COOKIE to proceed.")

def tg_post(method: str, data: dict, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, data=data, files=files, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = {"non_json_body": r.text[:500]}
    print(f"[tg] {method} {r.status_code} ok={j.get('ok')} desc={j.get('description')}")
    return r, j


def tg_send_text(text: str):
    return tg_post("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})


def build_caption(title: str, url: str, summary: str) -> str:
    cap = f"📌 <b>{title}</b>"
    if summary:
        cap += f"\n{summary}"
    cap += f"\n{url}"
    return cap[:900]

def download_bytes(url: str, referer: str) -> bytes | None:
    try:
        headers = {"Referer": referer}
        resp = SESSION.get(url, headers=headers, timeout=25)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:
        print(f"[warn] download failed: {url} err={e}")
    return None


def send_photo_url_or_file(url: str, caption: str | None, referer: str):
    if os.getenv("DOWNLOAD_AND_UPLOAD", "1").strip() == "1":
        data = download_bytes(url, referer)
        if data:
            files = {"photo": ("image.jpg", BytesIO(data))}
            return tg_post("sendPhoto", {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"}, files=files)
    return tg_post("sendPhoto", {"chat_id": TELEGRAM_CHAT_ID, "photo": url, "caption": caption or "", "parse_mode": "HTML"})


def send_video_url_or_file(url: str, caption: str | None, referer: str):
    if os.getenv("DOWNLOAD_AND_UPLOAD", "1").strip() == "1":
        data = download_bytes(url, referer)
        if data:
            files = {"video": ("video.mp4", BytesIO(data))}
            return tg_post("sendVideo", {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"}, files=files)
    return tg_post("sendVideo", {"chat_id": TELEGRAM_CHAT_ID, "video": url, "caption": caption or "", "parse_mode": "HTML"})

def summarize_text_from_html(soup: BeautifulSoup, max_chars=280) -> str:
    candidates = ["#bo_v_con", ".bo_v_con", "div.view_content", ".viewContent", "#view_content", "article", ".content", "#content"]
    container = next((soup.select_one(s) for s in candidates if soup.select_one(s)), soup)
    for tag in container(["script", "style", "noscript"]): tag.extract()
    text = re.sub(r"\s+", " ", container.get_text(" ", strip=True)).strip()
    return (text[:max_chars-1] + "…") if len(text) > max_chars else text


def parse_list() -> list[dict]:
    ensure_auth()
    r = SESSION.get(LIST_URL, timeout=25)
    html = get_encoding_safe_text(r)
    soup = BeautifulSoup(html, "html.parser")

    posts = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        u = urlparse(absolutize(LIST_URL, href))
        if u.netloc != urlparse(AVDBS_BASE).netloc:
            continue
        if not u.path.startswith("/board/"):
            continue
        if any(x in u.path.lower() for x in ("/write", "/login", "/member", "/search")):
            continue
        title = a.get_text(strip=True)
        key = f"{u.path}?{u.query}".strip("?")
        full_url = u.geturl()
        if key not in posts or (title and len(title) > len(posts[key]["title"])):
            posts[key] = {"id": key, "title": title or "(제목 없음)", "url": full_url}

    res = list(posts.values())
    res.sort(key=lambda x: x["url"], reverse=True)
    print(f"[debug] t22 list collected: {len(res)} items")
    return res

def parse_post(url: str) -> dict:
    r = SESSION.get(url, timeout=25)
    html = get_encoding_safe_text(r)
    soup = BeautifulSoup(html, "html.parser")
    summary = summarize_text_from_html(soup)

    candidates = ["#bo_v_con", ".bo_v_con", "div.view_content", ".viewContent", "#view_content", "article", ".content", "#content"]
    container = next((soup.select_one(s) for s in candidates if soup.select_one(s)), soup)

    images = []
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or img.get("data-echo")
        if not src:
            continue
        full = absolutize(url, src)
        if not is_excluded_image(full):
            images.append(full)

    if not images:
        for a in container.find_all("a", href=True):
            h = a["href"].strip()
            if re.search(r"\.(jpg|jpeg|png|gif|webp)(?:\?|$)", h, re.I):
                full = absolutize(url, h)
                if not is_excluded_image(full):
                    images.append(full)

    video_exts = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
    videos = []
    for v in container.find_all(["video", "source"]):
        s = v.get("src")
        if s:
            full = absolutize(url, s)
            if any(full.lower().endswith(ext) for ext in video_exts):
                videos.append(full)

    iframes = [absolutize(url, f.get("src")) for f in container.find_all("iframe") if f.get("src")]

    title = None
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        title = ogt.get("content").strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    images = list(dict.fromkeys(images))
    videos = list(dict.fromkeys(videos))
    iframes = list(dict.fromkeys(iframes))

    if os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1":
        print("[trace] images:", images[:10])
        print("[trace] videos:", videos[:5])
        try:
            preview = "\\n".join(images[:8]) or "(no images)"
            tg_send_text("🔍 t22 image candidates:\\n" + preview)
        except Exception:
            pass

    return {"images": images, "videos": videos, "iframes": iframes, "summary": summary, "title_override": title}

def process():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID is required")

    if ENABLE_HEARTBEAT:
        tg_send_text(HEARTBEAT_TEXT)

    posts = parse_list()
    print("[debug] posts top5:", [(p["title"][:20], p["url"]) for p in posts[:5]])

    seen = load_seen()
    to_send = []
    for p in posts:
        key = f"avdbs:t22:{p['id']}"
        if key not in seen:
            p["_seen_key"] = key
            to_send.append(p)

    if FORCE_SEND_LATEST and not to_send and posts:
        latest = posts[0]
        latest["_seen_key"] = f"avdbs:t22:{latest['id']}"
        to_send = [latest]
        print("[debug] FORCE_SEND_LATEST=1 → most recent 1 forced")

    if not to_send:
        print("[info] no new posts")
        return

    sent = []
    for p in to_send:
        title = p["title"]
        url   = p["url"]

        content = parse_post(url)
        if content.get("title_override"):
            title = content["title_override"]

        images, videos, iframes, summary = content["images"], content["videos"], content["iframes"], content["summary"]

        cap = build_caption(title, url, summary)
        tg_send_text(cap)
        time.sleep(1)

        for img in images:
            send_photo_url_or_file(img, None, url)
            time.sleep(1)
        for vid in videos:
            send_video_url_or_file(vid, None, url)
            time.sleep(1)
        if iframes:
            tg_send_text("🎥 임베드:\\n" + "\\n".join(iframes[:5]))
            time.sleep(1)

        sent.append(p["_seen_key"])

    append_seen(sent)
    print(f"[info] appended {len(sent)} keys")


if __name__ == "__main__":
    process()
