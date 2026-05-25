"""
marketplace.py — Claude Code skill marketplace browser
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

SOURCES = [
    {
        "id": "anthropics-skills",
        "url": "https://raw.githubusercontent.com/anthropics/skills/main/.claude-plugin/marketplace.json",
        "repo": "anthropics/skills",
        "branch": "main",
        "type": "skills",
    },
    {
        "id": "anthropics-community",
        "url": "https://raw.githubusercontent.com/anthropics/claude-plugins-community/main/.claude-plugin/marketplace.json",
        "repo": "anthropics/claude-plugins-community",
        "branch": "main",
        "type": "plugins",
    },
]

CACHE_TTL = 3600 * 24  # 24h
_HEADERS = {"User-Agent": "CoderFleet/1.0"}


class MarketplaceManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_json(self, url: str) -> dict | None:
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def _load_source(self, source: dict) -> list[dict]:
        cache_path = self.cache_dir / f"market_{source['id']}.json"
        fresh = cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL

        if fresh:
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return self._flatten(data, source)
            except Exception:
                pass

        data = self._fetch_json(source["url"])
        if data:
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        elif cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            return []

        return self._flatten(data, source)

    def _flatten(self, data: dict, source: dict) -> list[dict]:
        results = []
        for plugin in data.get("plugins", []):
            name        = plugin.get("name", "")
            desc        = plugin.get("description", "")
            raw_source  = plugin.get("source")
            skills      = plugin.get("skills", [])
            author_raw  = plugin.get("author", {})
            author_name = author_raw.get("name", "") if isinstance(author_raw, dict) else str(author_raw)
            homepage    = plugin.get("homepage", "")
            category    = plugin.get("category", "")

            if source["type"] == "skills" and skills:
                # anthropics/skills: each entry in "skills" → individual skill
                for skill_path in skills:
                    slug = skill_path.rstrip("/").split("/")[-1]
                    results.append({
                        "slug":       slug,
                        "name":       slug,
                        "description": desc,
                        "category":   category,
                        "author":     author_name or "Anthropic",
                        "source_id":  source["id"],
                        "repo":       source["repo"],
                        "branch":     source["branch"],
                        "skill_path": skill_path,
                        "source_raw": raw_source,
                        "homepage":   homepage,
                        "verified":   True,
                    })
            else:
                results.append({
                    "slug":       name,
                    "name":       name,
                    "description": desc,
                    "category":   category,
                    "author":     author_name,
                    "source_id":  source["id"],
                    "repo":       source["repo"],
                    "branch":     source["branch"],
                    "skill_path": None,
                    "source_raw": raw_source,
                    "homepage":   homepage,
                    "verified":   False,
                })
        return results

    async def search(self, q: str = "", category: str = "", limit: int = 40) -> list[dict]:
        loop = asyncio.get_event_loop()
        all_entries: list[dict] = []
        for source in SOURCES:
            entries = await loop.run_in_executor(None, self._load_source, source)
            all_entries.extend(entries)

        q_lower = q.lower().strip()
        results = []
        for p in all_entries:
            if q_lower:
                if q_lower not in p["name"].lower() and q_lower not in p["description"].lower():
                    continue
            if category and p["category"] != category:
                continue
            results.append(p)

        results.sort(key=lambda p: (
            not p["verified"],
            0 if (q_lower and q_lower in p["name"].lower()) else 1,
            p["name"],
        ))
        return results[:limit]

    def _resolve_skill_url(self, entry: dict) -> str | None:
        source_id  = entry.get("source_id", "")
        repo       = entry.get("repo", "")
        branch     = entry.get("branch", "main")
        source_raw = entry.get("source_raw")
        skill_path = entry.get("skill_path")

        if source_id == "anthropics-skills" and skill_path:
            path = skill_path.lstrip("./")
            return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}/SKILL.md"

        if isinstance(source_raw, str):
            path = source_raw.lstrip("./")
            return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}/SKILL.md"

        if isinstance(source_raw, dict):
            s_type   = source_raw.get("source", "")
            git_url  = source_raw.get("url", "")
            path     = source_raw.get("path", "")
            ref      = source_raw.get("ref", "main")
            repo_path = git_url.replace("https://github.com/", "").removesuffix(".git")

            if s_type == "git-subdir":
                skill_md = f"{path}/SKILL.md" if path else "SKILL.md"
                return f"https://raw.githubusercontent.com/{repo_path}/{ref}/{skill_md}"
            elif s_type == "url":
                return f"https://raw.githubusercontent.com/{repo_path}/{ref}/SKILL.md"

        return None

    def _download(self, url: str) -> str:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8")

    async def download_skill_md(self, entry: dict) -> str:
        url = self._resolve_skill_url(entry)
        if not url:
            raise ValueError("无法解析 SKILL.md 下载地址")
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._download, url)
        except urllib.error.HTTPError as e:
            raise ValueError(f"下载失败 ({e.code})，该插件可能不包含独立的 SKILL.md")
        except Exception as e:
            raise ValueError(f"下载失败：{e}")
