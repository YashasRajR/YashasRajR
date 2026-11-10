#!/usr/bin/env python3
"""Put a marble-machine header banner at the top of every repository's README.

    python scripts/add_repo_headers.py            # dry run: shows what would change
    python scripts/add_repo_headers.py --push     # clones each repo, edits README.md, commits and pushes

The banners themselves live in this profile repo (cards/headers/<repo>.svg) and are
redrawn by the daily Action, so each repository only needs this one-time edit. The
block sits between two markers, so running the script again updates it in place
rather than adding a second banner. Repositories without a README get a new one.

Needs git, signed in to GitHub (the same way you normally push). Run it after the
profile repo, with cards/headers, has been pushed, so the image links work.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- profile-card:start -->", "<!-- profile-card:end -->"


def block(profile, repo):
    base = profile.get("header_card_base",
                       f"https://raw.githubusercontent.com/{profile['login']}/{profile['login']}/main/cards/headers")
    title = profile.get("display_names", {}).get(repo["name"], repo["name"])
    return (f"{START}\n<p align=\"center\">\n"
            f"  <a href=\"https://github.com/{profile['login']}\"><img src=\"{base}/{repo['name']}.svg\" "
            f"width=\"100%\" alt=\"{title}\" /></a>\n</p>\n{END}\n")


def with_block(text, new_block):
    """Replace an existing banner, or put the banner at the very top."""
    if START in text and END in text:
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        return head + new_block.rstrip("\n") + tail
    return new_block + "\n" + text


def find_readme(folder):
    for name in os.listdir(folder):
        if name.lower() in ("readme.md", "readme.markdown", "readme"):
            return folder / name
    return None


def rmtree(path):
    def unlock(func, p, _):
        os.chmod(p, stat.S_IWRITE)   # git marks some files read-only on Windows
        func(p)
    shutil.rmtree(path, onerror=unlock)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def main():
    push = "--push" in sys.argv
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "data" / "github.json").read_text(encoding="utf-8"))
    exclude = set(profile.get("exclude", [])) | {profile["login"]}
    repos = [r for r in data.get("repos", []) if r["name"] not in exclude]
    if not (ROOT / "cards" / "headers").is_dir():
        sys.exit("cards/headers is missing. Run scripts/render_cards.py first.")

    print(f"{'Updating' if push else 'Dry run for'} {len(repos)} repositories\n")
    work = Path(tempfile.mkdtemp(prefix="profile-headers-"))
    changed = 0
    try:
        for repo in repos:
            name = repo["name"]
            folder = work / name
            try:
                git("clone", "--depth", "1", "--quiet", f"https://github.com/{profile['login']}/{name}.git", str(folder))
            except subprocess.CalledProcessError as e:
                print(f"  {name}: could not clone ({e.stderr.strip()[:80]})")
                continue
            readme = find_readme(folder)
            old = readme.read_text(encoding="utf-8") if readme else f"# {name}\n\n{repo.get('description', '')}\n"
            new = with_block(old, block(profile, repo))
            if readme and new == old:
                print(f"  {name}: already up to date")
                continue
            changed += 1
            action = "update banner" if START in old else ("add banner" if readme else "create README with banner")
            if not push:
                print(f"  {name}: would {action}")
                continue
            target = readme or folder / "README.md"
            target.write_text(new, encoding="utf-8")
            git("add", target.name, cwd=folder)
            git("commit", "--quiet", "-m", "Add profile header banner to README", cwd=folder)
            git("push", "--quiet", cwd=folder)
            print(f"  {name}: {action}, pushed")
    finally:
        rmtree(work)
    print(f"\n{changed} {'changed' if push else 'to change'}."
          + ("" if push else " Run again with --push to apply."))


if __name__ == "__main__":
    main()
