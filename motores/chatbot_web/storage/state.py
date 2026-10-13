import json
import sqlite3
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lease = open(path.with_suffix(".lock"), "a+b")
        if self.lease.tell() == 0:
            self.lease.write(b"0")
            self.lease.flush()
        self.lease.seek(0)
        try:
            import os

            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lease.close()
            raise RuntimeError(
                "Another service/CLI process owns this state database. Use its HTTP API or stop it first."
            ) from exc
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,depth INTEGER,status TEXT DEFAULT 'pending',
                hash TEXT,title TEXT,payload TEXT,error TEXT,checked TEXT,etag TEXT,modified TEXT);
            CREATE TABLE IF NOT EXISTS links(source TEXT,target TEXT,details TEXT,document INTEGER,
                PRIMARY KEY(source,target));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,mode TEXT,status TEXT,started TEXT,finished TEXT,report TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT OR IGNORE INTO meta VALUES('version','0');
        """)
        self.db.execute("UPDATE pages SET status='pending' WHERE status='processing'")
        self.db.commit()

    def discover(self, url, depth):
        self.db.execute(
            "INSERT OR IGNORE INTO pages(url,depth) VALUES(?,?)", (url, depth)
        )
        self.db.commit()

    def requeue(self):
        self.db.execute("UPDATE pages SET status='pending'")
        self.db.commit()

    def ensure_pipeline(self, revision):
        old = self.db.execute("SELECT value FROM meta WHERE key='pipeline'").fetchone()
        if not old or old[0] != revision:
            with self.db:
                # Preserve hashes; fetch HTML to compare newly extracted semantics.
                self.db.execute("UPDATE pages SET etag=NULL,modified=NULL")
                self.db.execute(
                    "INSERT OR REPLACE INTO meta VALUES('pipeline',?)", (revision,)
                )

    def dirty(self, url):
        self.db.execute(
            "UPDATE pages SET hash=NULL,etag=NULL,modified=NULL WHERE url=?", (url,)
        )
        self.db.commit()

    def claim(self):
        row = self.db.execute(
            "SELECT * FROM pages WHERE status='pending' ORDER BY depth,url LIMIT 1"
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE pages SET status='processing' WHERE url=?", (row["url"],)
            )
            self.db.commit()
        return dict(row) if row else None

    def page(self, url):
        row = self.db.execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def mark(self, url, status, error=None):
        self.db.execute(
            "UPDATE pages SET status=?,error=?,checked=? WHERE url=?",
            (status, error, now(), url),
        )
        self.db.commit()

    def save(self, url, page, hash_value, links, etag=None, modified=None):
        with self.db:
            self.db.execute(
                "UPDATE pages SET status='indexed',hash=?,title=?,payload=?,error=NULL,checked=?,etag=?,modified=? WHERE url=?",
                (
                    hash_value,
                    page["titulo"],
                    json.dumps(page, ensure_ascii=False),
                    now(),
                    etag,
                    modified,
                    url,
                ),
            )
            self.db.execute("DELETE FROM links WHERE source=?", (url,))
            self.db.executemany(
                "INSERT OR REPLACE INTO links VALUES(?,?,?,?)",
                [
                    (
                        url,
                        l["url"],
                        json.dumps(l, ensure_ascii=False),
                        int(l["documento"]),
                    )
                    for l in links
                ],
            )

    def promote_document(self, url):
        rows = list(
            self.db.execute("SELECT source,details FROM links WHERE target=?", (url,))
        )
        with self.db:
            for row in rows:
                link = json.loads(row["details"])
                link["documento"] = True
                self.db.execute(
                    "UPDATE links SET document=1,details=? WHERE source=? AND target=?",
                    (json.dumps(link, ensure_ascii=False), row["source"], url),
                )
                # Refetch parent to index the newly identified HTML reference on next update.
                self.db.execute(
                    "UPDATE pages SET etag=NULL,modified=NULL WHERE url=?",
                    (row["source"],),
                )
        if rows:
            self.bump()

    def document_links(self, urls):
        result = []
        for url in urls:
            result.extend(
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT details FROM links WHERE source=? AND document=1", (url,)
                )
            )
        return result

    @property
    def version(self):
        return int(
            self.db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
        )

    def bump(self):
        self.db.execute(
            "UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='version'"
        )
        self.db.commit()

    def job(self, id, mode, status, report=None):
        with self.db:
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,finished=excluded.finished,report=excluded.report",
                (
                    id,
                    mode,
                    status,
                    now(),
                    now() if status not in {"running", "queued"} else None,
                    json.dumps(report or {}, ensure_ascii=False),
                ),
            )

    def latest_job(self):
        row = self.db.execute(
            "SELECT * FROM jobs ORDER BY started DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["report"] = json.loads(result["report"])
        return result

    def pages(self, offset=0, limit=50, category=None):
        # Category filter uses json_each and supports all labels, not just primary.
        sql = "SELECT url,title,status,checked,error,payload FROM pages WHERE payload IS NOT NULL"
        args = []
        if category:
            sql += " AND EXISTS(SELECT 1 FROM json_each(pages.payload,'$.categorias') WHERE value=?)"
            args.append(category)
        sql += " ORDER BY url LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        result = []
        for row in self.db.execute(sql, args):
            item = dict(row)
            page = json.loads(item.pop("payload"))
            item.update(
                categorias=page["categorias"],
                fecha_publicacion=page["fecha_publicacion"],
                campos=page["campos"],
            )
            item["documentos"] = [
                json.loads(r[0])
                for r in self.db.execute(
                    "SELECT details FROM links WHERE source=? AND document=1",
                    (item["url"],),
                )
            ]
            result.append(item)
        return result

    def indexed_urls(self):
        return [
            row[0]
            for row in self.db.execute(
                "SELECT url FROM pages WHERE hash IS NOT NULL AND payload IS NOT NULL AND status!='gone'"
            )
        ]

    def title_matches(self, question, limit=12):
        import re
        from indexing.cleaner import folded

        stop = {
            "universidad",
            "unamba",
            "nacional",
            "micaela",
            "bastidas",
            "apurimac",
            "donde",
            "como",
            "cuales",
            "puedo",
            "para",
            "obtener",
            "encuentro",
            "funciona",
            "ofrece",
            "informacion",
            "encuentra",
            "proceso",
            "estudios",
            "tramitar",
        }
        terms = {
            t
            for t in re.findall(r"\w+", folded(question))
            if len(t) >= 4 and t not in stop
        }
        if not terms:
            return []
        ranked = []
        for row in self.db.execute(
            "SELECT url,title FROM pages WHERE hash IS NOT NULL AND payload IS NOT NULL AND status!='gone'"
        ):
            words = set(re.findall(r"\w+", folded(row["title"] + " " + row["url"])))
            score = len(terms & words)
            if score:
                ranked.append((score, row["url"]))
        return [url for _, url in sorted(ranked, reverse=True)[:limit]]

    def stats(self):
        counts = {
            r[0]: r[1]
            for r in self.db.execute(
                "SELECT status,COUNT(*) FROM pages GROUP BY status"
            )
        }
        return {
            "paginas": counts,
            "descubiertas": sum(counts.values()),
            "enlaces_documentales": self.db.execute(
                "SELECT COUNT(*) FROM links WHERE document=1"
            ).fetchone()[0],
            "relaciones": self.db.execute("SELECT COUNT(*) FROM links").fetchone()[0],
            "version_coleccion": self.version,
            "paginas_con_indice_guardado": self.db.execute(
                "SELECT COUNT(*) FROM pages WHERE hash IS NOT NULL AND payload IS NOT NULL AND status!='gone'"
            ).fetchone()[0],
        }

    def close(self):
        self.db.close()
        self.lease.close()
