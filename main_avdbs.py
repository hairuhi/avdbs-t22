# requirements: requests, bs4, lxml, cryptography
import os, json, time, re, io
import requests
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

BASE = "https://www.avdbs.com"
LIST_URL = f"{BASE}/board/t22"
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]; CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def load_cookies_to_session(sess):
    f = Fernet(os.environ["COOKIE_ENC_KEY"].encode())
    data = f.decrypt(open("state/cookies.enc.json","rb").read())
    st = json.loads(data)
    # storage_state to cookies
    for c in st.get("cookies", []):
        if "name" in c and "value" in c and c.get("domain"):
            sess.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path","/"))

def get(url, sess, **kw):
    r = sess.get(url, timeout=15, **kw)
    if r.status_code in (401,403):
        raise PermissionError("Auth required")
    if r.status_code == 429:
        raise RuntimeError("Rate limited")
    r.raise_for_status()
    return r

def send_text(cap):
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  json={"chat_id": CHAT_ID, "text": cap, "disable_web_page_preview": True}, timeout=20)

def send_file(bytes_, caption=None):
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                  files={"document": ("media", bytes_)},
                  data={"chat_id": CHAT_ID, "caption": caption or ""}, timeout=60)

def parse_list(html):
    soup = BeautifulSoup(html, "lxml")
    # 사이트 구조에 맞게 셀렉터 조정: 글 ID/제목/링크
    # return [(post_id, title, href), ...]
    ...

def parse_detail(html):
    soup = BeautifulSoup(html, "lxml")
    # 본문/이미지/영상 추출
    return {"text": "...", "images": [], "videos": []}

def run():
    os.makedirs("state", exist_ok=True)
    seen = set(open("state/seen_ids.txt").read().split()) if os.path.exists("state/seen_ids.txt") else set()
    s = requests.Session()
    load_cookies_to_session(s)
    html = get(LIST_URL, s).text
    posts = parse_list(html)

    new_posts = [p for p in posts if p[0] not in seen]
    for pid, title, href in new_posts:
        time.sleep(1.2)
        d = get(BASE+href, s).text
        parsed = parse_detail(d)
        cap = f"{title}\n{parsed['text'][:300]}...\n원문: {BASE+href}"
        send_text(cap)
        for u in parsed["images"]:
            # 항상 다운로드 후 업로드
            b = get(u, s).content
            send_file(io.BytesIO(b))
        # 영상/iframe 안내
        if parsed["videos"]:
            send_text(f"[영상 {len(parsed['videos'])}개] 원문에서 확인")
        seen.add(pid)

    open("state/seen_ids.txt","w").write("\n".join(sorted(seen)))

if __name__ == "__main__":
    run()
