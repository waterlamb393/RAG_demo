import asyncio
from playwright.async_api import async_playwright
import os
import logging
from urllib.parse import urljoin, urlparse, unquote
import re
import aiohttp
import psycopg2
import psycopg2.extras

# ★ 複数 URL をここに入れる
#START_URLS = [
#    "https://investors.nxera.life/",
#    "https://investors.nxera.life/jp/",
#    "https://www.nxera.life/",
#    "https://soseiheptares.blogspot.com/",
#]

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

# -----------------------------
# PostgreSQL 接続
# -----------------------------
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


# ★ URL デコード対応版 safe_filename
def safe_filename(url):
    decoded = unquote(url)
    fname = decoded.replace("https://", "").replace("http://", "")
    fname = re.sub(r'[\\/:*?"<>|]', '_', fname)
    fname = fname.replace("?", "_").replace("&", "_").replace("=", "_")
    return fname


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


async def extract_links(page, base_url):
    links = set()
    base_domain = urlparse(base_url).netloc  # ★ URLごとにドメイン判定

    hrefs = await page.eval_on_selector_all("a", "els => els.map(e => e.getAttribute('href'))")
    for href in hrefs:
        if not href or href.startswith("javascript"):
            continue
        absolute = urljoin(base_url, href)
        links.add(absolute)

    onclicks = await page.eval_on_selector_all("[onclick]", "els => els.map(e => e.getAttribute('onclick'))")
    for oc in onclicks:
        if not oc:
            continue

        m1 = re.search(r"router\.push\(['\"]([^'\"]+)['\"]\)", oc)
        if m1:
            links.add(urljoin(base_url, m1.group(1)))
            continue

        m2 = re.search(r"window\.open\(['\"]([^'\"]+)['\"]", oc)
        if m2:
            links.add(urljoin(base_url, m2.group(1)))
            continue

        m3 = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", oc)
        if m3:
            links.add(urljoin(base_url, m3.group(1)))
            continue

    data_hrefs = await page.eval_on_selector_all("[data-href]", "els => els.map(e => e.getAttribute('data-href'))")
    for href in data_hrefs:
        if href:
            links.add(urljoin(base_url, href))

    pdfs = {u for u in links if u.lower().endswith(".pdf")}

    # ★ base_url のドメインと一致する HTML のみクロール
    htmls = {
        u for u in links
        if not u.lower().endswith(".pdf")
        and urlparse(u).netloc == base_domain
    }

    return pdfs | htmls


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


async def worker(name, browser, queue, visited, visited_lock, db_conn):
    page = await browser.new_page()

    while True:
        url = await queue.get()
        if url is None:
            break

        if url.lower().endswith(".pdf"):
            await download_pdf_direct(url, db_conn=db_conn)
            async with visited_lock:
                visited.add(url)
            db_insert_url(db_conn, url)
            queue.task_done()
            continue

        async with visited_lock:
            if len(visited) >= MAX_PAGES:
                queue.task_done()
                continue
            if url in visited:
                queue.task_done()
                continue

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

            links = await extract_links(page, url)
            for link in links:
                await queue.put(link)

        except Exception as e:
            logging.error(f"Failed {url}: {e}")
            with open(f"{OUTPUT_DIR}/failed_urls.log", "a") as f:
                f.write(url + "\n")

        queue.task_done()

    await page.close()


async def generate_sitemap(visited):
    with open(f"{OUTPUT_DIR}/sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in visited:
            f.write(f"  <url><loc>{url}</loc></url>\n")
        f.write("</urlset>")


async def main():
# ★ 複数 URL をここに入れる
    START_URLS = [
    "https://investors.nxera.life/",
    "https://www.nxera.life/",
    "https://soseiheptares.blogspot.com/",
    ]

    queue = asyncio.Queue()
    visited = set()
    visited_lock = asyncio.Lock()

    # ★ 複数 URL を queue に投入
    for url in START_URLS:
        await queue.put(url)

    db_conn = get_db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        workers = [
            asyncio.create_task(worker(f"W{i}", browser, queue, visited, visited_lock, db_conn))
            for i in range(CONCURRENCY)
        ]

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers)

        await generate_sitemap(visited)

        await browser.close()

    db_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
