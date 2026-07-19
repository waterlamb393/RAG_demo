import asyncio
from playwright.async_api import async_playwright
import os
import logging
from urllib.parse import urljoin, urlparse, unquote
import re
import aiohttp
import psycopg2
import psycopg2.extras
import requests
#from ddgs import DDGS

OUTPUT_DIR = "crawl_output"
MAX_PAGES = 1000
CONCURRENCY = 3

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/text", exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/pdf", exist_ok=True)

logging.basicConfig(
    filename=f"{OUTPUT_DIR}/crawl.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ----------------------------------------
# DuckDuckGo Search
# ----------------------------------------
def getURLs(query):
    url = "https://api.search.brave.com/res/v1/web/search"
    BRAVE_API_KEY = "BSAQ2hZjho9973o0EaLHECZ1Q_5Xfu_"  
     
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    params = {
        "q": query,
        "count": 20,
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("web", {}).get("results", [])
        return [item["url"] for item in results]

    except Exception as e:
        print(f"Brave Search API error: {e}")
        logging.error(f"Brave Search API error: {e}")
        return []
    
# ----------------------------------------
# PostgreSQL
# ----------------------------------------
def get_db():
    return psycopg2.connect(
#        host="localhost",
        host="192.168.11.56",
        dbname="ragdb",
        user="postgres",
        password="hirorian77"
    )

def db_insert_url(conn, url):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO visited_urls (url) VALUES (%s) ON CONFLICT DO NOTHING;",
            (url,)
        )
    conn.commit()

def db_exists(conn, url):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM visited_urls WHERE url = %s;", (url,))
        return cur.fetchone() is not None

def db_get_last_modified(conn, url):
    with conn.cursor() as cur:
        cur.execute("SELECT last_modified FROM visited_urls WHERE url = %s;", (url,))
        row = cur.fetchone()
        return row[0] if row else None

def db_update_last_modified(conn, url, last_modified):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE visited_urls SET last_modified = %s WHERE url = %s;",
            (last_modified, url)
        )
    conn.commit()


# ----------------------------------------
# Utility
# ----------------------------------------
def safe_filename(url):
    decoded = unquote(url)
    fname = decoded.replace("https://", "").replace("http://", "")
    fname = re.sub(r'[\\/:*?"<>|]', '_', fname)
    fname = fname.replace("?", "_").replace("&", "_").replace("=", "_")
    return fname


async def fetch_last_modified(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.head(url, timeout=10) as resp:
                return resp.headers.get("Last-Modified")
    except:
        return None


# ----------------------------------------
# PDF Download
# ----------------------------------------
async def download_pdf_direct(url, referer=None, db_conn=None):
    exists = await asyncio.to_thread(db_exists, db_conn, url)
    if exists:
        print(f"Skip PDF (already in DB): {url}")
        logging.info(f"Skip PDF (already in DB): {url}")
        return

    print(f"Downloading PDF: {url}")
    logging.info(f"Downloading PDF: {url}")

    save_path = f"{OUTPUT_DIR}/pdf/{safe_filename(url)}.pdf"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,*/*",
    }
    if referer:
        headers["Referer"] = referer

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")

                with open(save_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

        print(f"Saved PDF: {save_path}")
        logging.info(f"Saved PDF: {save_path}")

        await asyncio.to_thread(db_insert_url, db_conn, url)

    except Exception as e:
        logging.error(f"PDF download failed {url}: {e}")
        with open(f"{OUTPUT_DIR}/failed_urls.log", "a") as f:
            f.write(url + "\n")


# ----------------------------------------
# Save HTML
# ----------------------------------------
async def save_all(page, url, db_conn=None):
    exists = await asyncio.to_thread(db_exists, db_conn, url)
    if exists:
        print(f"Skip HTML (already in DB): {url}")
        logging.info(f"Skip HTML (already in DB): {url}")
        return

    fname = safe_filename(url)
    body_text = await page.evaluate("document.body.innerText")

    with open(f"{OUTPUT_DIR}/text/{fname}.txt", "w", encoding="utf-8") as f:
        f.write(body_text)

    await asyncio.to_thread(db_insert_url, db_conn, url)


# ----------------------------------------
# Worker (Last-Modified 対応)
# ----------------------------------------
async def worker(name, browser, queue, visited, visited_lock, db_conn):
    page = await browser.new_page()

    while True:
        url = await queue.get()
        if url is None:
            break

        # PDF
        if url.lower().endswith(".pdf"):
            await download_pdf_direct(url, db_conn=db_conn)
            async with visited_lock:
                visited.add(url)
            db_insert_url(db_conn, url)
            queue.task_done()
            continue

        # -----------------------------
        # Last-Modified チェック
        # -----------------------------
        db_lm = db_get_last_modified(db_conn, url)
        current_lm = await fetch_last_modified(url)

        should_crawl = False

        if db_lm is None:
            should_crawl = True
            print(f"[{name}] New URL → crawl: {url}")
        elif current_lm is None:
            should_crawl = True
            print(f"[{name}] No Last-Modified header → crawl: {url}")
        elif db_lm != current_lm:
            should_crawl = True
            print(f"[{name}] Last-Modified changed → crawl: {url}")
        else:
            print(f"[{name}] Skip (Last-Modified unchanged): {url}")

        if not should_crawl:
            queue.task_done()
            continue

        # -----------------------------
        # Fetch
        # -----------------------------
        print(f"[{name}] Fetching: {url}")

        try:
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded"),
                timeout=20
            )

            await asyncio.sleep(1)

            await save_all(page, url, db_conn=db_conn)

            async with visited_lock:
                visited.add(url)

            db_insert_url(db_conn, url)

            # Last-Modified 保存
            db_update_last_modified(db_conn, url, current_lm)

        except Exception as e:
            logging.error(f"Failed {url}: {e}")
            with open(f"{OUTPUT_DIR}/failed_urls.log", "a") as f:
                f.write(url + "\n")

        queue.task_done()

    await page.close()


# ----------------------------------------
# Sitemap
# ----------------------------------------
async def generate_sitemap(visited):
    with open(f"{OUTPUT_DIR}/sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in visited:
            f.write(f"  <url><loc>{url}</loc></url>\n")
        f.write("</urlset>")


# ----------------------------------------
# Main
# ----------------------------------------
async def main():
    # getURLs にキーワードリストを渡して URL リストを取得する
    keywords = [
        "ネクセラファーマ パイプライン", "ネクセラファーマ", "Nxera Pharma",
        "NBI-1117567", "NBI-1117568", "NBI-1117569", "NBI-1117570",
        "Direclidine", "ORX750", "ORX142", "ORX489",
        "NXE0048149", "NXE0039732", "NXE0033744", "NXE0027477",
        "Serenza Therapeutics"
    ]

    # ★ キーワードごとに getURLs を呼び出して URL を集約
    START_URLS = []
    for kw in keywords:
        urls = getURLs(kw)
        print(f"[Keyword] {kw} → {len(urls)} URLs")
        START_URLS.extend(urls)

    # 重複削除
    START_URLS = list(set(START_URLS))

    print(f"Total collected URLs: {len(START_URLS)}")

    queue = asyncio.Queue()
    visited = set()
    visited_lock = asyncio.Lock()

    db_conn = get_db()

    # URL をキューに投入
    for url in START_URLS:
        print(f"Queue: {url}")
        await queue.put(url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        workers = [
            asyncio.create_task(worker(f"W{i}", browser, queue, visited, visited_lock, db_conn))
            for i in range(CONCURRENCY)
        ]

        # 全 URL を処理するまで待機
        await queue.join()

        # ワーカー終了用の None を投入
        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers)

        # サイトマップ生成
        await generate_sitemap(visited)

        await browser.close()

    db_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
