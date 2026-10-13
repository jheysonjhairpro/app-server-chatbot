from lxml import etree


def parse_sitemap(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    root = etree.fromstring(data, parser)
    kind = etree.QName(root).localname
    if kind not in {"urlset", "sitemapindex"}:
        raise ValueError("Invalid sitemap")
    return kind, root.xpath('//*[local-name()="loc"]/text()')


async def discover_sitemaps(fetcher, seeds, policy, max_maps=30, max_urls=2000):
    from urllib.parse import urlsplit

    pending = list(seeds)
    seen = set()
    urls = []
    errors = []
    while pending and len(seen) < max_maps and len(urls) < max_urls:
        url = pending.pop(0)
        if url in seen or not policy.allowed(url, auxiliary=True):
            continue
        seen.add(url)
        try:
            response = await fetcher.get(url, auxiliary=True, xml=True)
            if response.status != 200:
                continue
            data = response.body
            if data[:2] == b"\x1f\x8b":
                import zlib

                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = decompressor.decompress(
                    data, fetcher.settings.max_response_bytes + 1
                )
                if (
                    len(data) > fetcher.settings.max_response_bytes
                    or not decompressor.eof
                ):
                    raise ValueError(
                        "Compressed sitemap exceeds decompressed byte limit"
                    )
            kind, entries = parse_sitemap(data)
            if kind == "sitemapindex":
                pending.extend(entries[:max_maps])
            else:
                for entry in entries:
                    if len(urls) >= max_urls:
                        break
                    if policy.allowed(entry):
                        urls.append(entry)
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})
    return urls, errors
