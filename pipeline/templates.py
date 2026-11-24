"""LLM-free script writer: builds a valid, on-format script straight from verified facts.

Used when the LLM is unavailable (e.g. the free Cloudflare allocation is used up), so
a Reel still ships every slot. Less witty than the LLM, but every claim is a fact.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List

IST = timezone(timedelta(hours=5, minutes=30))


def short_title(title: str, limit: int = 30) -> str:
    """'MASHLE: MAGIC AND MUSCLES The Tri-Magicathlon ...' -> 'MASHLE'."""
    base = re.split(r"[:(\-–—]| Season | Part ", title)[0].strip() or title
    if len(base) <= limit:
        return base
    words, out = base.split(), ""
    for w in words:
        if len(out) + len(w) + 1 > limit:
            break
        out = f"{out} {w}".strip()
    return out or base[:limit]


def hashtag(text: str) -> str:
    return "#" + re.sub(r"[^A-Za-z0-9]", "", short_title(text, 18)).lower()[:22]


def spoken_time(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, IST)
    hour = dt.strftime("%I").lstrip("0")
    minute = "" if dt.minute == 0 else f":{dt.minute:02d}"
    return f"{dt.strftime('%A')} at {hour}{minute} {dt.strftime('%p')} IST"


def _facts_by_show(facts: List[dict]) -> Dict[str, List[dict]]:
    shows: Dict[str, List[dict]] = {}
    for f in facts:
        shows.setdefault(f["anime"], []).append(f)
    return shows


def news_script(script_id: str, format_name: str, facts: List[dict], media: List[dict], context: str) -> dict:
    by_show = _facts_by_show(facts)
    scenes = []
    hooks = {
        "This Week's Drops": ("THIS WEEK'S MUST-WATCH", "Your anime watchlist for this week is stacked. These are the episodes everyone will be talking about."),
        "Season Preview": ("NEXT SEASON IS STACKED", "Next anime season is already looking stacked. These are the shows fans are hyped for."),
        "Why It's Trending": ("EVERYONE IS WATCHING THIS", "Everyone is suddenly talking about the same anime. Here is why it is trending right now."),
        "Hype Check": ("WORTH THE HYPE?", "It is all over your feed right now. Let's check if it is actually worth the hype."),
        "Just Announced": ("ANIME COMING SOON", "These upcoming anime are already building serious buzz. Here is what is coming."),
    }
    head, hook_line = hooks.get(format_name, ("ANIME NEWS", "Here is the anime news you need this week."))
    first_title = media[0] if media else {}
    scenes.append({"role": "hook", "headline": head, "emphasis": [head.split()[-1]], "narration": hook_line,
                   "knowledge_ids": [], "image_style": "scene", "image_source": "official",
                   "image_subject": (first_title.get("title") or {}).get("english") or (first_title.get("title") or {}).get("romaji") or "",
                   "image_prompt": "a glowing anime-style TV screen in a dark room surrounded by floating anime posters"})
    limit = 1 if format_name in ("Why It's Trending", "Hype Check") else 4
    for m in media[:limit]:
        title = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
        show_facts = by_show.get(title, [])
        if not show_facts:
            continue
        name = short_title(title)
        ids, parts, tag = [], [], None
        nxt = m.get("nextAiringEpisode")
        for f in show_facts:
            if "next episode" in f["title"] and nxt:
                parts.append(f"Episode {nxt['episode']} of {name} drops {spoken_time(nxt['airingAt'])}.")
                tag = "AIRS " + datetime.fromtimestamp(nxt["airingAt"], IST).strftime("%a").upper()
                ids.append(f["id"])
            elif "studio" in f["title"]:
                parts.append(f["content"].replace(title, name))
                ids.append(f["id"])
            elif "score" in f["title"] and m.get("averageScore"):
                parts.append(f"Fans rate it {m['averageScore']} out of 100.")
                ids.append(f["id"])
            elif "premiere" in f["title"]:
                parts.append(f["content"].replace(title, name))
                ids.append(f["id"])
            if len(parts) >= 3:
                break
        if not parts:
            parts, ids = [show_facts[0]["content"].replace(title, name)], [show_facts[0]["id"]]
        airs = [x for x in parts if x.startswith("Episode ")]
        parts = airs + [x for x in parts if not x.startswith("Episode ")]  # the air time is the news: lead with it
        scene = {"role": "fact", "headline": name.upper()[:40], "emphasis": [], "narration": " ".join(parts),
                 "knowledge_ids": ids, "image_style": "scene", "image_source": "official", "image_subject": title,
                 "image_prompt": f"anime key visual inspired by {name}, dramatic lighting"}
        if tag:
            scene["tag"] = tag
        scenes.append(scene)
    scenes.append({"role": "outro", "headline": "WHICH ONE ARE YOU WATCHING?", "emphasis": ["WATCHING?"],
                   "narration": "Which one are you watching first? Tell me in the comments, and follow for daily anime news and secrets.",
                   "knowledge_ids": [], "image_style": "scene", "image_source": "ai",
                   "image_prompt": "anime fan silhouette on a rooftop at night watching a giant glowing city screen"})
    titles = [(m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") for m in media[:2]]
    tags = ["#anime", "#animenews"] + [hashtag(t) for t in titles if t] + ["#animerecommendations", "#otaku"]
    return {"id": script_id, "anime": "Anime", "mode": "fact", "mood": "hype", "format": format_name,
            "title": f"{format_name} · {datetime.now(IST).strftime('%d %b')}", "scenes": scenes,
            "caption": f"{hook_line} 🔥\n\nWhich one are you watching first? 👇", "hashtags": list(dict.fromkeys(tags))[:5]}


def evergreen_script(script_id: str, format_name: str, anime: str, facts: List[dict]) -> dict:
    picks = facts[:3]
    scenes = [{"role": "hook", "headline": f"{short_title(anime, 22).upper()} SECRETS", "emphasis": ["SECRETS"],
               "narration": f"You have watched {anime}, but I bet you did not know these three things. Number three is wild.",
               "knowledge_ids": [], "image_style": "scene", "image_source": "official", "image_subject": anime,
               "image_prompt": f"epic anime key visual inspired by {anime}"}]
    for f in picks:
        words = re.findall(r"[A-Za-z0-9'’-]+", f["title"])
        scenes.append({"role": "fact", "headline": " ".join(words[:5]).upper()[:40], "emphasis": [],
                       "narration": f["content"], "knowledge_ids": [f["id"]],
                       "image_style": "character" if f.get("characters") else "scene", "image_source": "ai",
                       "image_subject": (f.get("characters") or [anime])[0],
                       "image_prompt": f"anime illustration from {anime}: {f['content'][:220]}"})
    scenes.append({"role": "outro", "headline": "DID YOU KNOW THESE?", "emphasis": ["KNOW"],
                   "narration": "Which one surprised you the most? Tell me in the comments, and follow for more anime secrets every day.",
                   "knowledge_ids": [], "image_style": "scene", "image_source": "ai",
                   "image_prompt": f"wide cinematic anime scene inspired by {anime} at sunset"})
    tag = hashtag(anime)
    return {"id": script_id, "anime": anime, "mode": "fact", "mood": "hype", "format": format_name,
            "title": f"{anime} secrets you missed", "scenes": scenes,
            "caption": f"3 {anime} facts most fans never noticed 👀\n\nWhich one surprised you the most? 👇",
            "hashtags": list(dict.fromkeys(["#anime", tag, "#animefacts", "#otaku", "#animesecrets"]))[:5]}


def _words(script: dict) -> int:
    return sum(len(sc["narration"].split()) for sc in script["scenes"])


def template_script(script_id: str, format_name: str, anime: str, facts: List[dict], media: List[dict], context: str) -> dict:
    script = news_script(script_id, format_name, facts, media, context) if media else evergreen_script(script_id, format_name, anime, facts)
    # keep narration inside the 60-140 word window the validator enforces
    while _words(script) > 135 and len(script["scenes"]) > 4:
        script["scenes"].pop(-2)
    for sc in script["scenes"][1:-1]:
        if _words(script) <= 135:
            break
        sentences = re.split(r"(?<=[.!?]) ", sc["narration"])
        if len(sentences) > 1:
            sc["narration"] = " ".join(sentences[:-1])
    return script
