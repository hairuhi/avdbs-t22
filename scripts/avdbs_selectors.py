# avdbs t20용 셀렉터 정의 (HTML 구조에 따라 조정 가능)

SELECTORS = {
    # 로그인 관련
    "login_user": 'input[name="userid"], input#userid',
    "login_pass": 'input[name="passwd"], input#passwd',
    "login_submit": 'button[type="submit"], input[type="submit"], button.login',
    "login_ok_texts": ["로그아웃", "Logout"],

    # 게시판 목록
    "board_post_links": 'a.view-link, table a[href*="/board/"]',
    "page_param": "?page={page}",

    # 게시글 본문
    "post_title": "h1.title, .post-title, header h1",
    "post_body": "div.content, .post-content, article",
    "post_images": "div.content img, article img, .post-content img",
}
