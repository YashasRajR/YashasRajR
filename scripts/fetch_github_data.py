#!/usr/bin/env python3
"""Refresh data/github.json from the GitHub GraphQL API.

Needs a token in GH_TOKEN. The workflow passes the built-in GITHUB_TOKEN, which
sees public contributions. To count private contributions too, add a personal
access token (read:user scope) as the repository secret GH_STATS_TOKEN and turn
on "Private contributions" in your GitHub profile settings.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

QUERY = """
query($login: String!) {
  user(login: $login) {
    repositories(first: 100, privacy: PUBLIC, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name url description stargazerCount forkCount pushedAt
        primaryLanguage { name color }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

def graphql(query, variables, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit(f"GitHub API error: {body['errors'][0].get('message')}")
    return body["data"]


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN to a GitHub token.")
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    login = profile["login"]

    user = graphql(QUERY, {"login": login}, token)["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [{"date": d["date"], "count": d["contributionCount"]}
            for w in cal["weeks"] for d in w["contributionDays"]]

    repos = []
    for r in user["repositories"]["nodes"]:
        lang = r.get("primaryLanguage") or {}
        repos.append({"name": r["name"], "url": r["url"], "description": r["description"] or "",
                      "language": lang.get("name", ""), "languageColor": lang.get("color", ""),
                      "stars": r["stargazerCount"], "forks": r["forkCount"], "pushedAt": r["pushedAt"]})

    data = {
        "login": login,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "GitHub GraphQL API",
        "total": cal["totalContributions"],
        "public_repos": user["repositories"]["totalCount"],
        "days": days,
        "repos": repos,
    }
    out = ROOT / "data" / "github.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"{data['total']} contributions over {len(days)} days, {len(repos)} public repos")


if __name__ == "__main__":
    main()
