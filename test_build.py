from build import drop_reason, parent


def repo(name, created="2026-11-01T00:00:00Z", desc="x", stars=1000, forks=10, code_kb=500, hn_pts=0, readme=""):
    return {"full_name": name, "created_at": created, "description": desc, "stargazers_count": stars,
            "forks_count": forks, "code_kb": code_kb, "hn_pts": hn_pts, "readme": readme}


assert drop_reason(repo("a/a")) is None
assert drop_reason(repo("a/a", desc=None)) == "no description"
assert drop_reason(repo("a/a", desc=None, hn_pts=700)) is None  # HN interest beats a missing description
assert drop_reason(repo("a/a", forks=500)) == "fork ratio"
assert drop_reason(repo("a/a", code_kb=5)) == "tiny code"

known = {"orig/one": ("2026-10-20T00:00:00Z", 9000), "small/old": ("2026-10-20T00:00:00Z", 500),
         "young/two": ("2026-11-05T00:00:00Z", 9000), "old/posted": ("", float("inf"))}
assert parent(repo("me/copy", readme="based on https://github.com/Orig/One."), known) == "orig/one"
assert parent(repo("me/copy", readme="see github.com/old/posted.git"), known) == "old/posted"
assert parent(repo("me/hit", readme="related: github.com/small/old"), known) is None  # older but smaller: just a related link
assert parent(repo("me/orig", readme="community app: github.com/young/two"), known) is None  # younger
assert parent(repo("me/self", readme="github.com/me/self"), {"me/self": ("", float("inf"))}) is None
assert parent(repo("me/x", readme="github.com/unknown/repo"), known) is None
print("ok")
