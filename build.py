#!/usr/bin/env python3
"""Repo of the day: the best unseen repo that reached MIN_STARS within MAX_AGE_DAYS of creation.
Pipeline -> docs/data/YYYY-MM-DD.json (source of truth) -> static HTML in docs/.

  build.py                     build today's edition (no-op if it already exists)
  build.py --date YYYY-MM-DD   build a missed day (uses GitHub data as of now)
  build.py --regenerate        throw the day away and pick again
  build.py --render            re-render HTML from JSON only
  build.py --dry-run           don't write seen.json"""
import html, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MIN_STARS = 1000
MAX_AGE_DAYS = 30
MIN_CODE_KB = 100     # below this it's prompts, skill packs or lists, not software
MAX_FORK_RATIO = 0.4  # above this smells of mirrors or star farming
ROOT = Path(__file__).parent
SEEN = ROOT / "seen.json"
DOCS = ROOT / "docs"
DATA = DOCS / "data"
HEADERS = {"Accept": "application/vnd.github+json"}
if tok := os.environ.get("GITHUB_TOKEN"):
    HEADERS["Authorization"] = f"Bearer {tok}"


def get(url, headers=HEADERS, raw=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        body = "" if raw else "{}"
    return body if raw else json.loads(body)


def search():
    since = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    items = []
    for page in range(1, 11):  # ponytail: search API caps at 1000 results; fine while ~200 match
        qs = urllib.parse.urlencode({"q": f"created:>={since} stars:>={MIN_STARS}", "sort": "stars", "per_page": 100, "page": page})
        batch = get(f"https://api.github.com/search/repositories?{qs}")["items"]
        items += batch
        if len(batch) < 100:
            break
    return items


def hn(r):
    qs = urllib.parse.urlencode({"query": f"github.com/{r['full_name']}", "restrictSearchableAttributes": "url", "tags": "story"})
    top = max(get(f"https://hn.algolia.com/api/v1/search?{qs}", headers={})["hits"], key=lambda h: h["points"] or 0, default=None)
    r["hn_pts"] = (top and top["points"]) or 0
    r["hn_url"] = top and f"https://news.ycombinator.com/item?id={top['objectID']}"


def details(r):
    n = r["full_name"]
    r["code_kb"] = sum(get(f"https://api.github.com/repos/{n}/languages").values()) // 1024
    r["readme"] = get(f"https://api.github.com/repos/{n}/readme", {**HEADERS, "Accept": "application/vnd.github.raw"}, raw=True)


def drop_reason(r):
    if not r["description"] and not r["hn_pts"]:
        return "no description"
    if r["forks_count"] > MAX_FORK_RATIO * r["stargazers_count"]:
        return "fork ratio"
    if r["code_kb"] < MIN_CODE_KB:
        return "tiny code"


def parent(r, known):
    """Older, bigger known repo this one's README links to: it's a follower.
    known = {lower name: (created_at, stars)}; repos posted on earlier days count as ('', inf)."""
    me = r["full_name"].lower()
    for link in re.findall(r"github\.com/([\w.-]+/[\w.-]+)", r["readme"]):
        link = link.lower().removesuffix(".git").rstrip(".")
        if link == me or link not in known:
            continue
        created, stars = known[link]
        if created < r["created_at"] and stars > r["stargazers_count"]:
            return link


def per_day(r):
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).days
    return r["stargazers_count"] // max(age, 1), max(age, 1)


# --- README -> story text and picture (no LLM) ---

BODY_CHARS = 650  # characters, so CJK text (no spaces) gets the same budget as English


def is_image(url):
    """Real picture, not a badge: GitHub serves badges as SVG, so ask the host what it is."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10) as r:
            kind = r.headers.get("Content-Type", "")
    except Exception:
        return False
    return kind.startswith("image/") and "svg" not in kind


def readme_image(md, name):
    base = f"https://raw.githubusercontent.com/{name}/HEAD/"
    for m in re.finditer(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)|<img[^>]+src=[\"']([^\"']+)", md):
        line = md[md.rfind("\n", 0, m.start()) + 1:md.find("\n", m.end())]
        if line.count("![") + line.count("<img") > 1:  # a row of badges
            continue
        url = urllib.parse.urljoin(base, (m.group(1) or m.group(2)).replace("/blob/", "/raw/"))
        if is_image(url):
            return url
    return ""


def readme_body(md):
    """First prose paragraphs of the README, markdown stripped, about BODY_CHARS characters."""
    md = re.sub(r"```.*?```|<!--.*?-->", "", md, flags=re.S)
    paras, used = [], 0
    for block in re.split(r"\n\s*\n", md):
        if re.match(r"\s*(#|\||[-*+] |\d+\. |>|<table)", block) or re.search(r"^\s*[=-]{3,}\s*$", block, re.M):
            continue  # heading, table, list, quote
        link_words = sum(len(re.sub(r"<[^>]+>", "", x).split()) for x in re.findall(r"\[([^\]]*)\]\(|<a\b[^>]*>(.*?)</a>", block, re.S) for x in x)
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>", " ", block)  # images, html
        t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # links -> text
        t = re.sub(r"[`*_]{1,3}", "", t)
        t = " ".join(html.unescape(t).split())
        n = sum(any(c.isalnum() for c in w) for w in t.split())  # words, not separators like ·
        if (n < 8 and len(t) < 40) or link_words * 2 >= n:  # leftovers, nav rows (40 chars keeps CJK prose)
            continue
        left = BODY_CHARS - used
        if len(t) > left:
            t = (t[:left].rsplit(" ", 1)[0] if " " in t[:left] else t[:left]) + "…"
        paras.append(t)
        used += len(t)
        if used >= BODY_CHARS or len(paras) == 3:
            break
    return paras


# --- pipeline: GitHub -> one pick ---

def entry(r):
    pd, age = per_day(r)
    return {"name": r["full_name"], "owner": r["owner"]["login"], "url": r["html_url"], "description": r["description"] or "",
            "body": readme_body(r["readme"]), "image": readme_image(r["readme"], r["full_name"]),
            "stars": r["stargazers_count"], "stars_per_day": pd, "age_days": age, "language": r["language"] or "",
            "hn_points": r["hn_pts"], "hn_url": r["hn_url"]}


def pick(seen, day):
    """Best candidate not in seen. Records every repo it rules out in seen too, so they aren't rechecked."""
    candidates = search()
    names = {k.lower(): k for k in seen} | {r["full_name"].lower(): r["full_name"] for r in candidates}
    known = {k.lower(): ("", float("inf")) for k in seen} | {r["full_name"].lower(): (r["created_at"], r["stargazers_count"]) for r in candidates}
    fresh = [r for r in candidates if r["full_name"] not in seen]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(hn, fresh))
    fresh.sort(key=lambda r: (r["hn_pts"], per_day(r)[0]), reverse=True)
    for r in fresh:  # GitHub calls only until one passes: ~2 per repo looked at
        details(r)
        if why := drop_reason(r):
            seen[r["full_name"]] = {"date": day, "status": f"skip: {why}"}
        elif p := parent(r, known):
            seen[r["full_name"]] = {"date": day, "status": f"follows {names[p]}"}
        else:
            seen[r["full_name"]] = {"date": day, "status": "posted"}
            return {"date": day, "repo": entry(r)}
    return {"date": day, "repo": None}


# --- render: JSON -> static HTML ---

def esc(s):
    return html.escape(str(s), quote=True)


def long_date(d):
    d = date.fromisoformat(d)
    return f"{d:%A, %B} {d.day}, {d.year}"


def story(r):
    links = [f'<a href="{esc(r["url"])}">GitHub ›</a>'] + ([f'<a href="{esc(r["hn_url"])}">Discuss ›</a>'] if r["hn_points"] else [])
    meta = [f"★ {r['stars']:,}", f"+{r['stars_per_day']:,} a day", f"{r['age_days']} days old"]
    meta += [esc(r["language"])] * bool(r["language"]) + [f"{r['hn_points']} HN points"] * bool(r["hn_points"])
    img = f'<figure><img src="{esc(r["image"])}" alt="" referrerpolicy="no-referrer"></figure>' if r["image"] else ""
    dek = f'<p class="dek">{esc(r["description"])}</p>' if r["description"] else ""
    body = "".join(f"<p>{esc(t)}</p>" for t in r["body"])
    return (f'<article class="story top"><div class="kicker">• Repo of the day</div>'
            f'<div class="byline">By {esc(r["owner"])} · {" · ".join(links)}</div>'
            f'<h2><a href="{esc(r["url"])}">{esc(r["name"])}</a></h2>'
            f'<div class="top-cols">{img}<div>{dek}<div class="body">{body}</div><p class="meta">{" · ".join(meta)}</p></div></div></article>')


def page(title, desc, eyebrow, body):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lora:wght@700;900&family=PT+Serif:ital,wght@0,400;0,700;1,400&display=swap">
<link rel="stylesheet" href="style.css">
<script>try{{const t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script></head>
<body><div class="paper">
<div class="eyebrow">{eyebrow}</div>
<header class="mast"><h1><a href="index.html">The Daily Repo</a></h1>
<p class="sub">One open-source project that went viral on GitHub, every day</p></header>
<div class="bar"><input id="q" type="search" placeholder="Search every edition" aria-label="Search every edition" autocomplete="off"></div>
<main id="results" hidden></main><main id="content">{body}</main>
<footer>Picked from repos that reached {MIN_STARS:,} GitHub stars within {MAX_AGE_DAYS} days of creation · Each appears once · Text taken from each project's README</footer>
</div><script src="site.js"></script></body></html>
"""


def eyebrow(center):
    theme = '<button type="button" id="theme" aria-label="Toggle dark mode">☾</button>'
    return f'<div><a href="archive.html">← Archive</a></div><div class="center">{center}</div><div class="right">{theme}</div>'


def edition_page(ed, prev, nxt):
    r = ed["repo"]
    arrow = lambda d, sym, label: f'<a href="{d}.html" aria-label="{label}">{sym}</a>' if d else f'<span class="off">{sym}</span>'
    center = f'{arrow(prev, "‹", "Previous edition")}<span>{long_date(ed["date"])}</span>{arrow(nxt, "›", "Next edition")}'
    body = story(r) if r else '<p class="empty">Nothing new crossed the line today.</p>'
    title = f"{r['name']} · The Daily Repo" if r else f"The Daily Repo · {long_date(ed['date'])}"
    return page(title, r["description"] if r else "", eyebrow(center), body)


def render():
    eds = [json.loads(p.read_text()) for p in sorted(DATA.glob("????-??-??.json"), reverse=True)]
    dates = [ed["date"] for ed in eds]
    for i, ed in enumerate(eds):
        (DOCS / f"{ed['date']}.html").write_text(edition_page(ed, dates[i + 1] if i + 1 < len(dates) else None, dates[i - 1] if i else None))
    if eds:
        (DOCS / "index.html").write_text(edition_page(eds[0], dates[1] if len(dates) > 1 else None, None))
    else:
        (DOCS / "index.html").write_text(page("The Daily Repo", "", eyebrow("<span>No edition yet</span>"), '<p class="empty">No edition yet.</p>'))
    rows = "".join(f'<li><a href="{ed["date"]}.html">{long_date(ed["date"])}</a> <span class="meta">{esc(ed["repo"]["name"]) if ed["repo"] else "nothing new"}</span></li>' for ed in eds)
    (DOCS / "archive.html").write_text(page("The Daily Repo · Archive", "Every repo of the day", eyebrow("<span>Archive</span>"), f'<ul class="archive">{rows}</ul>'))
    search_index = [{k: v for k, v in ed["repo"].items() if k != "body"} | {"date": ed["date"]} for ed in eds if ed["repo"]]
    (DATA / "all.json").write_text(json.dumps(search_index, ensure_ascii=False, separators=(",", ":")))


def main():
    args = sys.argv[1:]
    DATA.mkdir(parents=True, exist_ok=True)
    if "--render" in args:
        return render()
    day = date.fromisoformat(args[args.index("--date") + 1]).isoformat() if "--date" in args else date.today().isoformat()
    path = DATA / f"{day}.json"
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    if path.exists():
        if "--regenerate" not in args:
            print(f"{day} already published; pass --regenerate to redo it")
            return
        seen = {k: v for k, v in seen.items() if v["date"] != day}  # forget what this day looked at
    ed = pick(seen, day)
    path.write_text(json.dumps(ed, indent=1, ensure_ascii=False) + "\n")
    if "--dry-run" not in args:
        SEEN.write_text(json.dumps(seen, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"{day}: {ed['repo']['name'] if ed['repo'] else 'nothing new'} ({sum(v['date'] == day for v in seen.values())} repos looked at)")
    render()


if __name__ == "__main__":
    main()
