from urllib.parse import urlsplit
from crawler.url_manager import is_document


def needs_javascript(html, page):
    return len(page["texto"]) < 120 and any(
        s in html.lower()
        for s in ["__next_data__", 'id="app"', 'id="root"', "enable javascript"]
    )


async def render(url, fetcher):
    # Every browser request is fulfilled through the guarded fetcher. No direct network
    # traffic, POSTs, service workers, PDFs or arbitrary third-party assets are permitted.
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")
        await context.add_init_script(
            "window.WebSocket = class { constructor() { throw Error('disabled'); } };"
        )

        async def route_handler(route):
            request = route.request
            try:
                if (
                    request.method != "GET"
                    or request.resource_type
                    not in {"document", "script", "xhr", "fetch"}
                    or is_document(request.url)
                ):
                    await route.abort()
                    return
                response = await fetcher.get(
                    request.url, resource=request.resource_type != "document"
                )
                await route.fulfill(
                    status=response.status,
                    body=response.body,
                    headers={
                        "content-type": response.headers.get(
                            "content-type", "text/html"
                        )
                    },
                )
            except Exception:
                await route.abort()

        await context.route("**/*", route_handler)
        try:
            page = await context.new_page()
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=fetcher.settings.request_timeout * 1000,
            )
            await page.wait_for_timeout(1500)
            html = await page.content()
            if len(html.encode()) > fetcher.settings.max_response_bytes:
                raise ValueError("Rendered DOM too large")
            return html
        finally:
            await browser.close()
