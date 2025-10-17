import os, re, time
from io import BytesIO
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AVDBS_COOKIE = os.getenv("AVDBS_COOKIE", "").strip()
LIST_URL = "https://www.avdbs.com/board/t22"
TIMEOUT = 25

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AVDBS-T22Bot/7.5)",
    "Accept-Language": "ko,en;q=0.8",
})

# ──────────────────────────────────────────────
# 쿠키 적용
def cookie_string_to_jar(raw: str):
    jar = requests.cookies.RequestsCookieJar()
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        jar.set(k.strip(), v.strip(), domain=".avdbs.com")
    SESSION.cookies.update(jar)

if AVDBS_COOKIE:
    cookie_string_to_jar(AVDBS_COOKIE)
# ──────────────────────────────────────────────

def tg_post(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, data=data, files=files, timeout=60)
    return r

def tg_send_text(text):
    return tg_post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def send_photo_file(bytes_data, caption=None):
    return tg_post("sendPhoto",
                   {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or "", "parse_mode": "HTML"},
                   files={"photo": ("image.jpg", BytesIO(bytes_data))})

def fetch_html(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.encoding = r.apparent_encoding or "utf-8"
    if "로그인" in r.text and "AVDBS" in r.text:
        print("[warn] login/adult page returned, check cookie validity")
    return r.text

def get_post_list():
    html = fetch_html(LIST_URL)
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/board/" not in href:
            continue
        full = urljoin(LIST_URL, href)
        title = a.get_text(strip=True)
        posts.append((title, full))
    # 중복 제거, 최신 순 정렬
    seen = set()
    result = []
    for t, u in posts[::-1]:
        if u not in seen:
            result.append((t, u))
            seen.add(u)
    return result[-3:]  # 최근 3개만 테스트

def parse_post(url):
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title else "(제목 없음)"
    body = soup.select_one("#bo_v_con, #view_content, .view_content, .board_view")
    summary = ""
    if body:
        summary = re.sub(r"\s+", " ", body.get_text(" ", strip=True))[:200]

    images = []
    if body:
        for img in body.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            if any(x in src.lower() for x in ["/logo", "/banner", "level/", "19cert", "placeholder"]):
                continue
            full = urljoin(url, src)
            images.append(full)
    return title, summary, list(dict.fromkeys(images))

def download_image(url):
    try:
        r = SESSION.get(url, headers={"Referer": LIST_URL}, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print(f"[warn] download fail: {url} {e}")
    return None

def process():
    posts = get_post_list()
    for title, link in posts:
        title, summary, images = parse_post(link)
        tg_send_text(f"📌 <b>{title}</b>\n{summary}\n{link}")
        time.sleep(1)
        for img in images[:10]:
            data = download_image(img)
            if data:
                send_photo_file(data)
                time.sleep(1)

if __name__ == "__main__":
    process()
