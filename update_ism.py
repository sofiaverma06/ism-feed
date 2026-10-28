#!/usr/bin/env python3
"""
update_ism.py - append the latest official ISM Manufacturing numbers to ism_history.csv.

Plain code, no LLM in the loop. Source = ISM's own press release on PR Newswire.
Writes ONLY if every gate passes. Anything odd -> exit non-zero and write nothing.

Gates (all must pass):
  a) the release is for the target month/year (headline AND "Manufacturing at a Glance" header)
  b) all four current values are between 25 and 75
  c) the prior-month values printed in the release match the last row already in the CSV
  d) target month is not already present, and it is exactly the month after the last row (no gaps)

Exit codes:
  0  appended OK, or nothing to do (already present / not published yet and still inside the window)
  1  LOUD failure: gate failed, parse failed, or still not published at the deadline day
  2  LOUD failure: could not fetch the listing or the release (network / block page)

Usage:
  python update_ism.py                      # target = previous calendar month (ET), writes the CSV
  python update_ism.py --dry-run            # do everything except write
  python update_ism.py --month 2026-09      # force a target month
  python update_ism.py --verify-last        # read-only: does the CSV's last row match the official release?
Test hooks: --listing-file, --release-file, --today YYYY-MM-DD
"""
import argparse, calendar, csv, datetime as dt, html, io, re, sys, time, urllib.request

LISTING_URL = "https://www.prnewswire.com/news/institute-for-supply-management/"
BASE = "https://www.prnewswire.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADER = ["date", "PMI", "New Orders", "Inventories", "Employment"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}


class Fail(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def log(msg):
    print(msg, flush=True)


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", errors="ignore")
                if r.status != 200:
                    raise Fail(f"HTTP {r.status} for {url}", 2)
                return body
        except Fail:
            raise
        except Exception as e:  # network error, 403, timeout...
            last = e
            time.sleep(2 * (i + 1))
    raise Fail(f"FETCH FAILED: {url}: {last}", 2)


def to_text(raw):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t))


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or [c.strip() for c in rows[0]] != HEADER:
        raise Fail(f"CSV header is not {HEADER}: {rows[0] if rows else 'empty file'}")
    data = [r for r in rows[1:] if r]
    if not data:
        raise Fail("CSV has no data rows")
    for r in data:
        if len(r) != 5:
            raise Fail(f"CSV row with wrong column count: {r}")
    return data


def month_of(date_str):
    y, m, _ = date_str.split("-")
    return int(y), int(m)


def next_month(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


def find_release_link(listing_html, y, m):
    name = MONTHS[m - 1]
    links = []
    for h in re.findall(r'href="(/news-releases/manufacturing-pmi-at-[^"]+)"', listing_html):
        if h not in links:
            links.append(h)
    if not links:
        raise Fail("FETCH FAILED: PR Newswire listing had no manufacturing-pmi links "
                   f"(page size {len(listing_html)}, starts: {to_text(listing_html)[:200]!r})", 2)
    for h in links:
        if f"-{name}-{y}-ism-manufacturing-pmi-report" in h:
            return h, links
    return None, links


def parse_glance(text, y, m):
    i = text.upper().find("MANUFACTURING AT A GLANCE")
    if i < 0:
        raise Fail("PARSE FAILED: 'Manufacturing at a Glance' not found in release text")
    seg = text[i:i + 2500]
    # header: "MANUFACTURING AT A GLANCE September 2026 Index Series Index Sep Series Index Aug"
    hm = re.search(r"GLANCE\s+([A-Za-z]+)\s+(\d{4})\s+Index\s+Series Index\s+([A-Za-z]{3})\s+Series Index\s+([A-Za-z]{3})", seg, re.I)
    if not hm:
        raise Fail("PARSE FAILED: could not read the table header months")
    hy, hmon = int(hm.group(2)), hm.group(1).lower()
    if hmon != MONTHS[m - 1] or hy != y:
        raise Fail(f"GATE a FAILED: table header says {hm.group(1)} {hm.group(2)}, expected {MONTHS[m-1].title()} {y}")
    cur_abbr, prev_abbr = hm.group(3).lower(), hm.group(4).lower()
    if ABBR.get(cur_abbr) != m:
        raise Fail(f"GATE a FAILED: column header '{cur_abbr}' is not the target month")
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    if ABBR.get(prev_abbr) != pm:
        raise Fail(f"GATE a FAILED: prior-month column '{prev_abbr}' is not the month before the target")
    num = r"(-?\d{1,2}(?:\.\d)?)"
    pats = {
        "PMI": r"Manufacturing PMI\s*®?\s*" + num + r"\s+" + num,
        "New Orders": r"(?<![A-Za-z'] )New Orders\s+" + num + r"\s+" + num,
        "Inventories": r"(?<!Customers' )(?<!Customers’ )Inventories\s+" + num + r"\s+" + num,
        "Employment": r"Employment\s+" + num + r"\s+" + num,
    }
    cur, prev = {}, {}
    for k, p in pats.items():
        mm = re.search(p, seg)
        if not mm:
            raise Fail(f"PARSE FAILED: could not read the '{k}' row")
        cur[k], prev[k] = float(mm.group(1)), float(mm.group(2))
    return cur, prev


def previous_month_et(today):
    y, m = today.year, today.month
    return (y - 1, 12) if m == 1 else (y, m - 1)


def verify_last(a):
    """Read-only check: does the LAST row in the CSV match the official release for that month
    (current values) and does the release's prior month match the second-to-last row?"""
    rows = read_csv(a.csv)
    if len(rows) < 2:
        raise Fail("need at least 2 rows to verify")
    y, m = month_of(rows[-1][0])
    listing = open(a.listing_file, encoding="utf-8", errors="ignore").read() if a.listing_file else fetch(LISTING_URL)
    link, links = find_release_link(listing, y, m)
    if link is None:
        raise Fail(f"VERIFY: release for {y}-{m:02d} (last CSV row) is not on the listing")
    release_html = open(a.release_file, encoding="utf-8", errors="ignore").read() if a.release_file else fetch(BASE + link)
    cur, prev = parse_glance(to_text(release_html), y, m)
    keys = ["PMI", "New Orders", "Inventories", "Employment"]
    csv_cur = dict(zip(keys, map(float, rows[-1][1:5])))
    csv_prev = dict(zip(keys, map(float, rows[-2][1:5])))
    bad_cur = {k: (cur[k], csv_cur[k]) for k in keys if abs(cur[k] - csv_cur[k]) > 0.05}
    bad_prev = {k: (prev[k], csv_prev[k]) for k in keys if abs(prev[k] - csv_prev[k]) > 0.05}
    if bad_cur or bad_prev:
        raise Fail(f"VERIFY MISMATCH for {rows[-1][0]} (release vs csv): current {bad_cur or 'ok'}, prior-month {bad_prev or 'ok'}. "
                   f"Source: {BASE}{link}")
    log(f"VERIFY OK: {rows[-1][0]} row {rows[-1][1:5]} matches the official release; prior month matches too. {BASE}{link}")
    return 0


def run(a):
    if a.verify_last:
        return verify_last(a)
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(
        __import__("zoneinfo").ZoneInfo("America/New_York")).date()
    ty, tm = (int(a.month[:4]), int(a.month[5:7])) if a.month else previous_month_et(today)
    label = f"{ty}-{tm:02d}"
    rows = read_csv(a.csv)
    last_y, last_m = month_of(rows[-1][0])
    log(f"target month {label}; CSV has {len(rows)} rows, last = {rows[-1][0]}")

    # gate d (part 1): already present?
    present = {month_of(r[0]) for r in rows}
    if (ty, tm) in present:
        if (last_y, last_m) == (ty, tm):
            log(f"ISM {label} already in the feed - nothing to do.")
            return 0
        raise Fail(f"GATE d FAILED: {label} is present but is not the last row (file out of order?)")
    # gate d (part 2): exactly the next month, no gaps
    if next_month(last_y, last_m) != (ty, tm):
        raise Fail(f"GATE d FAILED: last row is {last_y}-{last_m:02d}; refusing to append {label} (gap or out of order)")

    listing = open(a.listing_file, encoding="utf-8", errors="ignore").read() if a.listing_file else fetch(LISTING_URL)
    link, links = find_release_link(listing, ty, tm)
    if link is None:
        heads = [h.split("/news-releases/")[1].rsplit("-", 1)[0] for h in links[:3]]
        if today.day >= a.deadline_day:
            raise Fail(f"STILL NOT PUBLISHED on day {today.day}: {label} release is not on the listing. Top headlines: {heads}")
        log(f"{label} release not on the listing yet (inside the window, will retry). Top 3 on listing: {heads}")
        return 0
    log(f"found release: {BASE}{link}")

    release_html = open(a.release_file, encoding="utf-8", errors="ignore").read() if a.release_file else fetch(BASE + link)
    cur, prev = parse_glance(to_text(release_html), ty, tm)   # gate a (table header) inside

    # gate b: ranges
    for k, v in cur.items():
        if not (25.0 <= v <= 75.0):
            raise Fail(f"GATE b FAILED: {k}={v} outside 25-75")

    # gate c: prior month in release must equal last CSV row
    last = rows[-1]
    csv_prev = dict(zip(["PMI", "New Orders", "Inventories", "Employment"], map(float, last[1:5])))
    bad = {k: (prev[k], csv_prev[k]) for k in prev if abs(prev[k] - csv_prev[k]) > 0.05}
    if bad:
        raise Fail("GATE c FAILED: prior-month values in the release do not match the last CSV row "
                   f"(release vs csv): {bad}. NOT writing. Investigate (annual seasonal revision? bad row?)")
    log(f"gate c ok: release's prior month {prev} == csv row {last[0]}")

    line = f"{ty}-{tm:02d}-{calendar.monthrange(ty, tm)[1]:02d},{cur['PMI']:.1f},{cur['New Orders']:.1f},{cur['Inventories']:.1f},{cur['Employment']:.1f}"
    log(f"all gates passed. row: {line}")
    log(f"source: {BASE}{link}")
    if a.dry_run:
        log("dry run - nothing written.")
        return 0
    with open(a.csv, "rb") as f:
        raw = f.read()
    with open(a.csv, "ab") as f:
        if not raw.endswith(b"\n"):
            f.write(b"\n")
        f.write((line + "\n").encode())
    log(f"appended to {a.csv}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="ism_history.csv")
    ap.add_argument("--month", help="target YYYY-MM (default: previous calendar month, ET)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-last", action="store_true",
                    help="read-only: check the CSV's last row against the official release for that month")
    ap.add_argument("--deadline-day", type=int, default=5,
                    help="if still unpublished on/after this day of the month, fail loudly (default 5)")
    ap.add_argument("--today", help="test hook: YYYY-MM-DD")
    ap.add_argument("--listing-file", help="test hook")
    ap.add_argument("--release-file", help="test hook")
    a = ap.parse_args()
    try:
        sys.exit(run(a))
    except Fail as e:
        log(f"ERROR: {e}")
        sys.exit(e.code)


if __name__ == "__main__":
    main()
