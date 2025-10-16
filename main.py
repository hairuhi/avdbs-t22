import os, re, time, pathlib
from io import BytesIO
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AVDBS_BASE = os.getenv("AVDBS_BASE", "https://www.avdbs.com").rstrip("/")
AVDBS_BOARD_PATH = os.getenv("AVDBS_BOARD_PATH", "/board/t22")
LIST_URL = f"{AVDBS_BASE}{AVDBS_BOARD_PATH}"
AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()

SEEN_FILE = "state/avdbs_t22_seen.txt"
TIMEOUT = 25
TRACE_IMAGE_DEBUG = os.getenv("TRACE_IMAGE_DEBUG", "0").strip() == "1"

EXCLUDE_IMAGE_SUBSTRINGS = [
    "/logo/", "/banner/", "/ads/", "/noimage", "/favicon", "/thumb/", "/placeholder/", "/loading", ".svg",
    "/img/level/", "mb3_", "avdbs_logo", "main-search", "new_9x9w.png", "/img/19cert/", "19_cert", "19_popup",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-t22Bot/6.1)",
    "Accept-Language": "ko,en;q=0.8",
    "Referer": AVDBS_BASE + "/",
})

def cookie_string_to_jar(raw):
    jar = requests.cookies.RequestsCookieJar()
    base_host = urlparse(AVDBS_BASE).hostname or "www.avdbs.com"
    cdn_host = "i1.avdbs.com"
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        for dom in [base_host, "." + base_host.lstrip("."), cdn_host, "." + cdn_host]:
            jar.set(k, v, domain=dom)
    return jar

if AVDBS_COOKIE:
    SESSION.cookies.update(cookie_string_to_jar(AVDBS_COOKIE))
    ck = AVDBS_COOKIE.lower()
    if "adult_chk=1" not in ck and "adult=ok" not in ck:
        print("[warn] adult cookie not found → you may see placeholder images")

def absolutize(base_url, url):
    if not url: return ""
    if url.startswith("//"): return "https:" + url
    return urljoin(base_url, url)

def is_excluded_image(url): return any(h in url.lower() for h in EXCLUDE_IMAGE_SUBSTRINGS)

def download_bytes(url, referer):
    try:
        headers = {"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
        r = SESSION.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content
        print(f"[warn] download {r.status_code}: {url}")
    except Exception as e:
        print(f"[warn] download failed: {url} err={e}")
    return None

def tg_post(method, data, files=None):
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}", data=data, files=files, timeout=60)
    print(f"[tg] {method} {r.status_code}")
    return r

def tg_send_text(text):
    return tg_post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def send_photo_url_or_file(url, caption, referer):
    data = download_bytes(url, referer)
    if not data:
        print(f"[warn] skip photo: download failed → {url}")
        return
    tg_post("sendPhoto", {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"},
            files={"photo": ("image.jpg", BytesIO(data))})

def summarize_text(soup, max_chars=200):
    body = soup.select_one("#bo_v_con") or soup.select_one("article") or soup
    for t in body(["script", "style", "noscript"]): t.extract()
    txt = re.sub(r"\s+", " ", body.get_text(" ", strip=True))
    return (txt[:max_chars] + "…") if len(txt) > max_chars else txt

def parse_post(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            full = absolutize(url, src)
            if not is_excluded_image(full):
                images.append(full)
    images = list(dict.fromkeys(images))
    if TRACE_IMAGE_DEBUG:
        print("TRACE:", images[:10])
        tg_send_text("\n".join(images[:10]))
    summary = summarize_text(soup)
    title = soup.title.string.strip() if soup.title else "(제목 없음)"
    return title, summary, images

def process():
    r = SESSION.get(LIST_URL, timeout=TIMEOUT)
    soup = BeautifulSoup(r.text, "html.parser")
    posts = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/board/" not in href: continue
        full = absolutize(LIST_URL, href)
        posts.append(full)
    if not posts:
        print("[info] no posts found")
        return
    latest = posts[0]
    title, summary, images = parse_post(latest)
    tg_send_text(f"📌 <b>{title}</b>\n{summary}\n{latest}")
    for img in images[:10]:
        send_photo_url_or_file(img, None, latest)
        time.sleep(1)

if __name__ == "__main__":
    process()
