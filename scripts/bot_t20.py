#!/usr/bin/env python3
import os, json, time, re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from tqdm import tqdm
from playwright.sync_api import sync_playwright
from telegram import Bot, InputMediaPhoto
from telegram.constants import ParseMode
from avdbs_selectors import SELECTORS as S

# 환경변수 (GitHub Secrets로 제공)
BASE = "https://www.avdbs.com"
USERNAME = os.getenv("AVDBS_USERNAME")
PASSWORD = os.getenv("AVDBS_PASSWORD")
BOARD_URL = os.getenv("BOARD_URL", "https://www.avdbs.com/board/t20")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 실행 옵션
MAX_PAGES = 3
REQUEST_DELAY = 1.0
BATCH_SIZE = 10
HEADLESS = True

STATE_PATH = os.path.join("data", "state.json")
os.makedirs("data", exist_ok=True)
bot = Bot(token=TG_TOKEN)

def load_state():
    if not os.path.exists(STATE_PATH):
        return {"seen_posts": []}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sanitize_filename(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()[:100] or "untitled"

def telegram_caption(title, summary, part=None, total=None):
    cap = f"📌 {title.strip()}"
    if part and total:
        cap += f" ({part}/{total})"
    if summary:
        cap += f"\n\n{summary.strip()[:900]}"
    return cap

def chunk(lst, n):  # 리스트를 n개씩 나누기
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def login_and_get_context(p):
    browser = p.chromium.launch(headless=HEADLESS)
    context = browser.new_context()
    page = context.new_page()

    page.goto(urljoin(BASE, "/member/login"))
    time.sleep(1)
    try:
        page.fill(S["login_user"], USERNAME)
        page.fill(S["login_pass"], PASSWORD)
        page.click(S["login_submit"])
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception as e:
        print("⚠️ 로그인 셀렉터 확인 필요:", e)

    ok = any(page.locator(f"text={t}").count() > 0 for t in S["login_ok_texts"])
    print("✅ 로그인 성공" if ok else "❓ 로그인 불확실")
    return browser, context, page

def collect_post_links(page):
    urls = set()
    for num in range(1, MAX_PAGES + 1):
        target = BOARD_URL + S["page_param"].format(page=num)
        page.goto(target)
        page.wait_for_load_state("networkidle")
        for a in page.locator(S["board_post_links"]).all():
            href = a.get_attribute("href")
            if href and "/board/" in href:
                urls.add(urljoin(BASE, href))
        time.sleep(0.5)
    return list(urls)

def parse_post_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one(S["post_title"])
    body_el = soup.select_one(S["post_body"])
    images_el = soup.select(S["post_images"]) or []
    title = title_el.get_text(strip=True) if title_el else "untitled"
    summary = body_el.get_text(" ", strip=True)[:150] + "..." if body_el else ""
    imgs = [urljoin(BASE, i.get("src")) for i in images_el if i.get("src")]
    return title, summary, imgs

def send_images(title, summary, img_bytes):
    total = (len(img_bytes) + BATCH_SIZE - 1) // BATCH_SIZE
    for i, part in enumerate(chunk(img_bytes, BATCH_SIZE), start=1):
        media = []
        for j, b in enumerate(part):
            if j == 0:
                media.append(InputMediaPhoto(b, caption=telegram_caption(title, summary, i, total), parse_mode=ParseMode.HTML))
            else:
                media.append(InputMediaPhoto(b))
        bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
        time.sleep(0.5)

def main():
    state = load_state()
    seen = set(state.get("seen_posts", []))

    with sync_playwright() as p:
        browser, context, page = login_and_get_context(p)
        try:
            post_urls = collect_post_links(page)
            new_urls = [u for u in post_urls if u not in seen]
            if not new_urls:
                print("✅ 신규 글 없음")
                return

            print(f"🆕 신규 {len(new_urls)}건 발견")
            for u in tqdm(new_urls):
                page.goto(u)
                page.wait_for_load_state("networkidle", timeout=10000)
                title, summary, img_urls = parse_post_html(page.content())

                img_bytes = []
                for img in img_urls:
                    try:
                        resp = page.request.get(img, timeout=20000)
                        if resp.ok:
                            img_bytes.append(resp.body())
                    except:
                        continue

                if img_bytes:
                    send_images(title, summary, img_bytes)

                seen.add(u)
                state["seen_posts"] = list(seen)
                save_state(state)
                time.sleep(REQUEST_DELAY)
        finally:
            browser.close()

if __name__ == "__main__":
    if not all([USERNAME, PASSWORD, BOARD_URL, TG_TOKEN, TG_CHAT_ID]):
        raise SystemExit("❌ Secrets 값이 부족합니다. (USERNAME/PASSWORD/BOARD_URL/TOKEN/CHAT_ID)")
    main()
