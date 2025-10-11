# requirements: playwright, cryptography
import json, os
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

KEY = os.environ["COOKIE_ENC_KEY"].encode()  # base64 urlsafe 32-byte key
LOGIN_URL = "https://www.avdbs.com/..."      # 실제 로그인 엔드포인트로 교체

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="networkidle")
    # page.fill("#id", os.environ["AVDBS_ID"]); page.fill("#pw", os.environ["AVDBS_PW"])
    # 성인인증 체크/버튼 클릭 등 사이트 구조에 맞게 구현
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    ctx.storage_state(path="state/auth.json")
    browser.close()

f = Fernet(KEY)
enc = f.encrypt(open("state/auth.json","rb").read())
open("state/cookies.enc.json","wb").write(enc)
