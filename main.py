
import os
import re
import json
import time
import pathlib
import hashlib
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# ========= Telegram Secrets =========
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ========= AVDBS Settings (this project is dedicated to: /board/t22) =========
AVDBS_BASE  = "https://www.avdbs.com"
LIST_PATH   = "/board/t22"
T_CODE      = "t22"  # used for dedup key

# Optional auth (recommended to see full content)
AVDBS_ID     = os.getenv("AVDBS_ID", "").strip()
AVDBS_PW     = os.getenv("AVDBS_PW", "").strip()
AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()  # "PHPSESSID=...; adult_chk=1; ..."

# ========= State / Heartbeat =========
SEEN_FILE = os.getenv("SEEN_SET_FILE", "state/seen_ids.txt")  # key format: avdbs:{t_code}:{sha1(url)}
ENABLE_HEARTBEAT = os.getenv("ENABLE_HEARTBEAT", "0").strip() == "1"
HEARTBEAT_TEXT   = os.getenv("HEARTBEAT_TEXT", "🧪 Heartbeat: bot is alive.")

# ========= HTTP =========
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22-bot/1.0; +https://github.com/your/repo)",
    "Accept-Language": "ko,ko-KR;q=0.9,en;q=0.8",
    "Connection": "close",
    "Referer": AVDBS_BASE,
})
TIMEOUT = 20

def ensure_state_dir():
    pathlib.Path("state").mkdir(parents=True, exist_ok=True)

def load_seen() -> set:
    ensure_state_dir()
    s = set()
    p = pathlib.Path(SEEN_FILE)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        s.add(line)
        except Exception:
            pass
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

def absolutize(base: str, url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base, url)

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

# ---------- AVDBS auth helpers ----------
def set_manual_cookie(cookie_str: str):
    # "a=1; b=2;" → session cookies
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        SESSION.cookies.set(k.strip(), v.strip(), domain=urlparse(AVDBS_BASE).netloc)

def is_login_wall(html: str) -> bool:
    h = html.lower()
    return ("로그인" in h and "회원" in h) or ("성인" in h and "인증" in h) or ("login" in h and "member" in h)

def try_login() -> bool:
    SESSION.headers["Referer"] = AVDBS_BASE
    SESSION.cookies.set("adult_chk", "1", domain=urlparse(AVDBS_BASE).netloc)

    candidates = [
        ("https://www.avdbs.com/member/login", {"user_id": AVDBS_ID, "user_pw": AVDBS_PW}),
        ("https://www.avdbs.com/member/login", {"mb_id": AVDBS_ID, "mb_password": AVDBS_PW}),
        ("https://www.avdbs.com/login",        {"id": AVDBS_ID, "pw": AVDBS_PW}),
    ]
    for url, payload in candidates:
        try:
            r = SESSION.post(url, data=payload, timeout=TIMEOUT)
            html = get_encoding_safe_text(r).lower()
            if r.status_code == 200 and ("logout" in html or "로그아웃" in html or "my page" in html):
                print(f"[avdbs] login success via {url}")
                return True
            if r.status_code in (301, 302, 303, 307, 308):
                home = SESSION.get(AVDBS_BASE, timeout=TIMEOUT)
                h = get_encoding_safe_text(home).lower()
                if "logout" in h or "로그아웃" in h or "my page" in h:
                    print(f"[avdbs] login success (redirect) via {url}")
                    return True
        except Exception as e:
            print(f"[avdbs] login attempt failed: {url} err={e}")
    print("[avdbs] login failed")
    return False

def avdbs_get(url: str) -> requests.Response:
    if AVDBS_COOKIE:
        set_manual_cookie(AVDBS_COOKIE)
    r = SESSION.get(url, timeout=TIMEOUT)
    html = get_encoding_safe_text(r)
    if r.status_code in (401, 403) or is_login_wall(html):
        print("[avdbs] login wall detected → login")
        ok = False
        if AVDBS_ID and AVDBS_PW:
            ok = try_login()
        elif AVDBS_COOKIE:
            print("[avdbs] provided cookie seems invalid/expired")
        if ok:
            r = SESSION.get(url, timeout=TIMEOUT)
    return r

# ---------- Parsing ----------
def fetch_list() -> list[dict]:
    base_url = urljoin(AVDBS_BASE, LIST_PATH)
    r = avdbs_get(base_url)
    html = get_encoding_safe_text(r)
    soup = BeautifulSoup(html, "html.parser")

    posts = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        absu = absolutize(base_url, href)
        u = urlparse(absu)
        if u.netloc != urlparse(AVDBS_BASE).netloc:
            continue
        if u.path.rstrip("/") == urlparse(base_url).path.rstrip("/"):
            continue
        if "page=" in u.query.lower():
            continue
        if "/board/" not in u.path:
            continue
        title = a.get_text(strip=True) or (u.path.rstrip("/").split("/")[-1] or absu)
        key = sha1(absu)
        if key not in posts or (title and len(title) > len(posts[key]["title"])):
            posts[key] = {"url": absu, "title": title}
    res = list(posts.values())
    print(f"[debug] list fetched({LIST_PATH}): {len(res)} items")
    return res

def text_summary_from_html(soup: BeautifulSoup, max_chars: int = 280) -> str:
    candidates = [".xe_content", "#bd_view", ".rd_body", "article", "#bo_v_con", ".bo_v_con", "div.view_content"]
    container = None
    for sel in candidates:
        node = soup.select_one(sel)
        if node:
            container = node
            break
    if container is None:
        container = soup
    for tag in container(["script", "style", "noscript"]):
        tag.extract()
    text = container.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:max_chars - 1] + "…") if (text and len(text) > max_chars) else (text or "")

def fetch_content_media_and_summary(post_url: str) -> dict:
    r = avdbs_get(post_url)
    html = get_encoding_safe_text(r)
    soup = BeautifulSoup(html, "html.parser")

    summary = text_summary_from_html(soup, max_chars=280)

    candidates = [".xe_content", "#bd_view", ".rd_body", "article", "#bo_v_con", ".bo_v_con", "div.view_content"]
    container = None
    for sel in candidates:
        node = soup.select_one(sel)
        if node:
            container = node
            break
    if container is None:
        container = soup

    images = []
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or img.get("data-echo")
        if not src:
            continue
        images.append(absolutize(post_url, src))

    video_exts = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
    videos = []
    for v in container.find_all(["video", "source"]):
        src = v.get("src")
        if not src:
            continue
        src = absolutize(post_url, src)
        if any(src.lower().endswith(ext) for ext in video_exts):
            videos.append(src)

    iframes = []
    for f in container.find_all("iframe"):
        src = f.get("src")
        if src:
            iframes.append(absolutize(post_url, src))

    images = list(dict.fromkeys(images))
    videos = list(dict.fromkeys(videos))
    iframes = list(dict.fromkeys(iframes))

    title = None
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        title = ogt.get("content").strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    return {"images": images, "videos": videos, "iframes": iframes, "summary": summary, "title_override": title}

# ---------- Telegram ----------
def tg_post(method: str, data: dict):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, data=data, timeout=20)
    try:
        j = r.json()
    except Exception:
        j = {"non_json_body": r.text[:500]}
    print(f"[tg] {method} status={r.status_code} ok={j.get('ok')} desc={j.get('description')}")
    return r, j

def tg_send_text(text: str):
    return tg_post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    })

def tg_send_media_group(media_items: list[dict]):
    return tg_post("sendMediaGroup", {
        "chat_id": TELEGRAM_CHAT_ID,
        "media": json.dumps(media_items, ensure_ascii=False)
    })

def build_caption(title: str, url: str, summary: str, batch_idx: int | None, total_batches: int | None) -> str:
    prefix = f"📌 <b>{title}</b>"
    if batch_idx is not None and total_batches is not None and total_batches > 1:
        prefix += f"  ({batch_idx}/{total_batches})"
    body = f"\n{summary}" if summary else ""
    suffix = f"\n{url}"
    caption = f"{prefix}{body}{suffix}"
    if len(caption) > 900:
        caption = caption[:897] + "…"
    return caption

# ---------- Main ----------
def process():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID is required")

    if ENABLE_HEARTBEAT:
        tg_send_text(HEARTBEAT_TEXT)

    posts = fetch_list()
    posts.sort(key=lambda x: x["url"])

    seen = load_seen()
    to_send = []
    for p in posts:
        key = f"avdbs:{T_CODE}:{hashlib.sha1(p['url'].encode('utf-8')).hexdigest()}"
        if key not in seen:
            p["_seen_key"] = key
            to_send.append(p)

    if not to_send:
        print("[info] no new posts")
        return

    sent_keys = []
    for p in to_send:
        title = p["title"]
        url   = p["url"]

        media = fetch_content_media_and_summary(url)
        if media.get("title_override"):
            title = media["title_override"]

        images = media["images"]
        videos = media["videos"]
        iframes = media["iframes"]
        summary = media["summary"]

        media_urls = images + videos
        MAX_ITEMS = 10

        if not media_urls:
            caption = build_caption(title, url, summary, None, None)
            tg_send_text(caption)
            sent_keys.append(p["_seen_key"])
            time.sleep(1)
            continue

        total = len(media_urls)
        total_batches = (total + MAX_ITEMS - 1) // MAX_ITEMS

        for batch_idx in range(total_batches):
            start = batch_idx * MAX_ITEMS
            end = min(start + MAX_ITEMS, total)
            chunk = media_urls[start:end]

            media_items = []
            for i, murl in enumerate(chunk):
                typ = "video" if any(murl.lower().endswith(ext) for ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v")) else "photo"
                item = {"type": typ, "media": murl}
                if batch_idx == 0 and i == 0:
                    item["caption"] = build_caption(title, url, summary, batch_idx + 1, total_batches)
                    item["parse_mode"] = "HTML"
                elif i == 0 and total_batches > 1:
                    item["caption"] = f"({batch_idx + 1}/{total_batches}) 계속"
                media_items.append(item)

            r, j = tg_send_media_group(media_items)
            if not j.get("ok"):
                tg_send_text(build_caption(title, url, summary, batch_idx + 1, total_batches))
            time.sleep(1)

        if iframes:
            tg_send_text("🎬 임베드 동영상 링크:\n" + "\n".join(iframes[:5]))

        sent_keys.append(p["_seen_key"])
        time.sleep(1)

    append_seen(sent_keys)
    print(f"[info] appended {len(sent_keys)} keys")

if __name__ == "__main__":
    process()
