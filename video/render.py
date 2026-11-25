"""Render a Reel from a script JSON.

  python -m video.render content/samples/one_piece_luffy.json
  python -m video.render content/samples/one_piece_luffy.json --stills   # quick design preview

Outputs in output/<script id>/:
  video.mp4  cover.jpg  captions.srt  timeline.json  manifest.json  (+ intermediate wavs)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import soundfile as sf

from .imagegen import CloudflareImageGenerator, ImageGenError, QuotaExceeded, build_prompt
from .official_art import VisionChecker, character_art, show_art, verify_image
from .media import MediaAsset, Openverse, build_blocklist, credits_text, find_image, find_music
from .script import ScriptError, full_caption, knowledge_ids, load_script
from .sfx import SR as SFX_SR
from .sfx import build_sfx_track
from .tts import Narrator, to_srt
from .validate import validate_video
from . import audio_library
from .cinematic import CinematicRenderer
from .shots import resolve_shots
from .style import resolve_style
from .visuals import FPS, H, W, FrameRenderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANNEL_FILE = PROJECT_ROOT / "channel.json"
MUSIC_DIR = PROJECT_ROOT / "assets" / "music"
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


AI_DISCLOSURE = ("🎨 Artwork: AI-generated fan art. Characters and series belong to their creators; "
                 "this is an unofficial fan page.")


OFFICIAL_ART_NOTE = "🖼️ Key art © its studio and publisher."


def log(msg: str) -> None:
    print(f"[render] {msg}", flush=True)


def channel_config() -> dict:
    return json.loads(CHANNEL_FILE.read_text(encoding="utf-8")) if CHANNEL_FILE.exists() else {}


def sources_for(ids: List[str], extra_records: Optional[List[dict]] = None) -> tuple:
    """(sources_note, unverified_ids, missing_ids) from the knowledge base (+ live news facts)."""
    try:
        from rag.knowledge import load_knowledge
        records = {r["id"]: r for r in load_knowledge()}
        records.update({r["id"]: r for r in (extra_records or [])})
    except Exception as exc:  # knowledge problems shouldn't block a design preview
        log(f"warning: could not load knowledge ({exc})")
        return "", [], ids
    missing = [i for i in ids if i not in records]
    used = [records[i] for i in ids if i in records]
    unverified = [r["id"] for r in used if not r["verified"]]
    hosts = sorted({urlparse(r["source_url"]).netloc.replace("en.", "") for r in used})
    licenses = sorted({r.get("license") for r in used if r.get("license")})
    note = f"Sources: {', '.join(hosts)}" + (f" ({', '.join(licenses)})" if licenses else "") if hosts else ""
    return note, unverified, missing


def pick_music(script: dict) -> Optional[Path]:
    mood_dir = MUSIC_DIR / script.get("mood", "default")
    candidates = sorted(p for d in (mood_dir, MUSIC_DIR) if d.is_dir() for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not candidates:
        return None
    idx = int(hashlib.sha1(script["id"].encode()).hexdigest(), 16) % len(candidates)
    return candidates[idx]


def run(cmd: List[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:4])} ...\n{result.stderr[-2000:]}")


def mix_audio(voice: Path, sfx: Path, music: Optional[Path], duration: float, out: Path, music_volume: float = 0.30) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice), "-i", str(sfx)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        graph = (
            "[0:a]aresample=48000,asplit=2[v1][v2];"
            "[1:a]volume=0.9[s];"
            f"[2:a]aresample=48000,atrim=0:{duration:.3f},volume={music_volume:.3f},"
            f"afade=t=out:st={max(0, duration - 1.5):.3f}:d=1.5[m];"
            "[m][v2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[md];"
            "[v1][s][md]amix=inputs=3:normalize=0,"
        )
    else:
        graph = "[0:a]aresample=48000[v1];[1:a]volume=0.9[s];[v1][s]amix=inputs=2:normalize=0,"
    graph += f"apad,atrim=0:{duration:.3f}[out]"
    premix = out.with_name("premix.wav")
    cmd += ["-filter_complex", graph, "-map", "[out]", "-ac", "2", "-ar", "48000", str(premix)]
    run(cmd)
    loudnorm_two_pass(premix, out)


LOUDNESS = "I=-14:TP=-1.5:LRA=11"


def loudnorm_two_pass(src: Path, dst: Path) -> None:
    """Two-pass EBU R128 normalisation to -14 LUFS (single pass undershoots on short clips)."""
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", f"loudnorm={LOUDNESS}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    stats_text = probe.stderr[probe.stderr.rfind("{"): probe.stderr.rfind("}") + 1]
    try:
        m = json.loads(stats_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not read loudness stats: {probe.stderr[-500:]}") from exc
    measured = (f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
                f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-af", f"loudnorm={LOUDNESS}:{measured},aresample=48000", "-ar", "48000", "-ac", "2", str(dst)])


def encode(renderer: FrameRenderer, audio: Path, out: Path) -> None:
    total = int(round(renderer.duration * FPS))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "14M", "-bufsize", "28M",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.time()
    try:
        for n in range(total):
            proc.stdin.write(renderer.frame(n / FPS).tobytes())
            if n and n % (FPS * 5) == 0:
                log(f"  frames {n}/{total} ({(time.time() - started) / n * 1000:.0f} ms/frame)")
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed:\n{err[-2000:]}")


def blocklist_terms():
    """(phrases, character names) that must never be image search topics."""
    phrases: List[str] = []
    names: List[str] = []
    try:
        from rag.knowledge import load_knowledge, load_metadata
        for entry in load_metadata():
            phrases += [entry["name"], *entry.get("aliases", []), *entry.get("image_block_terms", [])]
        for record in load_knowledge():
            phrases.append(record["anime"])
            names += record["characters"]
    except Exception as exc:
        log(f"warning: could not build full blocklist ({exc})")
    return phrases, names


def gather_media(script: dict, cache: Path, images: bool, music: bool, music_picker=None, recent_music=()):
    """Per-scene images and a music track from Openverse (local assets/music wins for music)."""
    client = Openverse()
    phrases, names = blocklist_terms()
    blocklist = build_blocklist(phrases + [script["anime"]], names)
    scene_assets: List[Optional[MediaAsset]] = []
    used: set = set()
    generator = CloudflareImageGenerator.from_env() if images else None
    vision = VisionChecker.from_env() if images else None
    quota_hit = False
    for index, scene in enumerate(script["scenes"]):
        queries = scene.get("visual_queries") or []
        asset = None
        if images and scene.get("image_source") == "official":
            asset = official_asset(scene, script, index, cache, vision)
        if asset is None and generator and scene.get("image_prompt") and not quota_hit:
            try:
                started = time.time()
                path, model = generator.generate(build_prompt(scene["image_prompt"], script["anime"]),
                                                 cache, seed=1000 + index,
                                                 character=scene.get("image_style") == "character")
                asset = MediaAsset("ai_image", str(path), scene["headline"], "", "AI-generated",
                                   "", model.rsplit("/", 1)[-1], f"ai-{index}")
                log(f"AI art scene {index + 1}: {model.rsplit('/', 1)[-1]} ({time.time() - started:.1f}s)")
            except QuotaExceeded as exc:
                quota_hit = True
                log(f"warning: {exc}; falling back to licensed photos")
            except ImageGenError as exc:
                log(f"warning: AI art failed for scene {index + 1} ({exc})")
        if asset is None and images and queries:
            try:
                asset = find_image(client, queries, cache, blocklist, used)
            except ValueError:
                raise
            except Exception as exc:  # network trouble: fall back to the typographic layout
                log(f"warning: image search failed for {queries} ({exc})")
            if asset:
                used.add(asset.openverse_id)
            else:
                log(f"no suitable image for {queries}; using typographic layout")
        scene_assets.append(asset)

    local = pick_music(script)
    music_asset: Optional[MediaAsset] = None
    library_tried = False
    if music and not local and audio_library.music_candidates(script.get("format", ""), script.get("mood", ""), recent_music):
        library_tried = True  # the licensed library has tracks for this format: the Supervisor's verdict is final
        music_asset = audio_library.pick_music(script.get("format", ""), script.get("mood", ""), script["id"],
                                               cache / "audio", recent_music, picker=music_picker)
    if music and not local and not library_tried:
        try:
            music_asset = find_music(client, script.get("mood", "default"), script["id"], cache,
                                     keywords=script.get("music_keywords", ""), picker=music_picker,
                                     format_name=script.get("format", ""))
        except Exception as exc:
            log(f"warning: music search failed ({exc})")
    return scene_assets, local, music_asset


def reset(inspector):
    """Fresh duplicate memory before re-picking shots (the same Reel is assembled again)."""
    if inspector is not None:
        inspector.seen.clear()
    return inspector


def run_visual_qa(script, narration, style, renderer, gemini, channel: dict, beats, repick, rounds: int = 2) -> dict:
    """Visual QA agent: watch -> ban wrong shots -> re-pick -> watch again. Returns the report (+ final renderer)."""
    from .visual_qa import review

    if gemini is None:
        return {"status": "skipped (no GEMINI_API_KEY)"}
    banned: set = set()
    report: dict = {}
    fixed = 0
    for attempt in range(rounds + 1):
        result = review(renderer, narration, script, gemini, [sh.source for sh in renderer.shots])
        if result is None:
            if report.get("issues") and fixed and report.get("up_to_mark"):
                # wrong shots were replaced on an edit that already met the bar; only the re-check could not run
                report.update(status="fixed (re-check unavailable)", fixed_issues=report.pop("issues"), issues=[])
            report.setdefault("status", "unavailable (Gemini busy)")
            break
        log(f"visual QA round {attempt + 1}: {len(result['bad'])} wrong shot(s), edit score {result['edit_score']}/10 - "
            f"{result['summary']}")
        for issue in result["issues"]:
            log(f"  {issue}")
        report = {"status": "checked", "summary": result["summary"], "issues": result["issues"],
                  "edit_score": result["edit_score"], "up_to_mark": result["up_to_mark"], "edit_notes": result["edit_notes"],
                  "rounds": attempt + 1, "fixed": fixed, "fixed_issues": report.get("fixed_issues", []) + report.get("issues", [])
                  if attempt else []}
        # weak shots: when the edit is below 8/10, act on the editor's "show this instead" suggestions
        weak = [(i, better) for i, better in result.get("weak", []) if i not in result["bad"]]
        if result["edit_score"] is not None and result["edit_score"] >= 8:
            weak = []
        if (not result["bad"] and not weak) or attempt == rounds:
            break
        for i, better in weak:
            shot = renderer.shots[i]
            scene_index = next((k for k, sc in enumerate(narration.scenes) if sc.start <= shot.start < sc.end + 0.01),
                               len(narration.scenes) - 1)
            plan = script["scenes"][scene_index].setdefault("shots", [])
            plan.insert(0, {"subject": better, "scene": better, "source": "wiki"})
            if shot.source:
                result["bad_sources"].append(shot.source)
        log(f"  editor asks for {len(weak)} better shot(s): " + "; ".join(b for _, b in weak)[:300]) if weak else None
        new = set(result["bad_sources"]) - banned
        if not new:
            break  # nothing new to ban (e.g. the only available picture): leave it to the QA gate
        banned |= new
        fixed += len(new)
        shots = repick(banned)
        renderer = CinematicRenderer(script, narration, style, shots, handle=channel.get("handle", ""), beats=beats)
    report["renderer"] = renderer
    report["calls"] = gemini.calls
    return report


def gather_clips(script: dict, cache: Path, style: dict, inspector, accounts) -> List[str]:
    """Short anime video clips (official trailer / official accounts' Reels about this show), inspected."""
    from .clips import find_clips
    from .official_art import anilist_gallery

    try:
        gallery = anilist_gallery(script["anime"])
        names = [n for n, _, _ in gallery.get("characters", [])][:8]
        wanted = int(sum(1 for _ in script["scenes"]) * 3 * style["clip_ratio"]) + 2
        return find_clips(script["anime"], cache / "clips", style["grading"], inspector, list(accounts or []),
                          gallery.get("trailer_id"), names, max_clips=min(20, wanted),
                          show_accounts=gallery.get("instagram", []))
    except Exception as exc:
        log(f"warning: clips unavailable ({type(exc).__name__}: {str(exc)[:160]})")
        return []


def official_asset(scene: dict, script: dict, index: int, cache: Path, vision) -> Optional[MediaAsset]:
    """Official key art / character portrait for a scene, only if it passes verify_image."""
    subject = (scene.get("image_subject") or script["anime"]).strip()
    try:
        if scene.get("image_style") == "character":
            path = character_art(subject, script["anime"], cache)
            kind, background = "portrait", None
            if path:
                art = show_art(script["anime"], cache) if script["anime"] != "Anime" else {}
                background = art.get("banner") or art.get("poster")
        else:
            art = show_art(subject, cache)
            path, kind, background = art.get("poster"), "poster", art.get("banner")
    except Exception as exc:
        log(f"warning: official art lookup failed for {subject!r} ({exc})")
        return None
    if not path:
        log(f"scene {index + 1}: no official art for {subject!r}")
        return None
    ok, reason = verify_image(Path(path), kind, subject, vision)
    log(f"scene {index + 1}: official {kind} for {subject!r} -> {'OK' if ok else 'REJECTED'} ({reason})")
    if not ok:
        return None
    return MediaAsset("official_image", str(path), subject, "", "Official key art", str(background or ""), "anilist", f"official-{index}")


def render(script_path: str, out_root: str = "output", voice: Optional[str] = None,
           speed: Optional[float] = None, stills: bool = False,
           images: bool = True, music: bool = True, extra_records: Optional[List[dict]] = None,
           music_picker=None, recent_music=()) -> dict:
    script = load_script(script_path)
    try:
        from integrations.instagram import load_env_file
        load_env_file(PROJECT_ROOT / ".env")  # CF_* credentials for local runs
    except Exception:
        pass
    channel = channel_config()
    out = Path(out_root) / script["id"]
    out.mkdir(parents=True, exist_ok=True)
    # a fixed channel voice (owner's choice) beats the Director's suggestion
    voice = voice or channel.get("fixed_voice") or script.get("voice") or channel.get("voice", "am_fenrir")
    style = resolve_style(script.get("style"))
    cinematic = style["layout"] == "cinematic"
    # the style owns pacing in the cinematic layout; classic keeps the old voice speed
    speed = speed or (style["voice_speed"] if cinematic else None) or script.get("speed") or channel.get("speed", 1.1)
    log(f"style: {style['name']} ({style['layout']}, voice x{speed})")

    ids = knowledge_ids(script)
    sources_note, unverified, missing = sources_for(ids, extra_records)

    t0 = time.time()
    from .tts import AuraVoice

    narrator = Narrator(aura=AuraVoice.from_env() if channel.get("voice_provider", "aura") == "aura" else None)
    if channel.get("random_voice", True) and not channel.get("fixed_voice") and not script.get("voice_locked"):
        narration = narrator.narrate_random(script["scenes"], seed=script["id"], speed=speed)
        voice = f"{narrator.backend}:{narrator.voice_used}"
    else:
        narration = narrator.narrate(script["scenes"], voice=voice, speed=speed)
    duration = narration.duration
    log(f"narration: {voice} - {duration:.1f}s audio in {time.time() - t0:.1f}s")
    (out / "timeline.json").write_text(json.dumps(narration.timeline_dict(), indent=2), encoding="utf-8")
    (out / "captions.srt").write_text(to_srt(narration.scenes), encoding="utf-8")

    scene_assets, local_music, music_asset = gather_media(script, Path(out_root) / "_media_cache", images, music, music_picker, recent_music)
    image_specs = [
        None if a is None else
        {"path": a.path, "layout": "poster", "background": a.source_url or None} if a.kind == "official_image" else
        {"path": a.path, "layout": "full", "credit": "AI ART" if channel.get("show_ai_label") else ""}
        if a.kind == "ai_image" else
        {"path": a.path, "credit": f"Photo: {a.creator or 'unknown'} · {a.license}"}
        for a in scene_assets
    ]
    log(f"images: {sum(1 for a in scene_assets if a)}/{len(scene_assets)} scenes")
    on_screen_sources = sources_note if channel.get("show_sources_on_video") else ""
    image_rejections: List[str] = []
    visual_qa: dict = {}
    gemini = None
    if cinematic:
        cache = Path(out_root) / "_media_cache"
        backup = []
        if images and script["anime"] != "Anime":
            try:
                art = show_art(script["anime"], cache)
                backup = [str(p) for p in (art.get("banner"), art.get("poster")) if p]
            except Exception as exc:
                log(f"warning: show art lookup failed ({exc})")
        # stock photos are a last resort in the cinematic look: fan-wiki shots of the actual subject beat them
        primary = [a.path if a and a.kind in ("official_image", "ai_image") else None for a in scene_assets]
        stock = [a.path for a in scene_assets if a and a.kind == "image"]
        from .image_check import Inspector

        inspector = Inspector.create() if images else None
        clips: List[str] = []
        if images and channel.get("use_clips", True) and style["clip_ratio"] > 0 and script["anime"] != "Anime":
            # footage only from OFFICIAL accounts (channel.json clip_accounts) - never other creators' edits,
            # which carry their own captions and watermarks
            clips = gather_clips(script, cache, style, inspector, channel.get("clip_accounts", ["crunchyroll"]))
        from .visual_qa import Gemini, approve_clips

        gemini = Gemini.from_env() if images else None
        clip_scenes = None
        if clips:
            approval = approve_clips(gemini, clips, [sc.get("narration", "") for sc in script["scenes"]], script["anime"]) \
                if gemini else None
            if approval is None:  # no clip editor available: clips only as generic hook/outro motion
                clip_scenes = {c: None for c in clips}
                log("clip editor unavailable: clips limited to hook/outro")
            else:
                clips = [c for c in clips if c in approval]
                clip_scenes = approval
        shots = resolve_shots(script, narration, style, primary, cache, backup, extra_records, stock,
                              inspector=inspector, clips=clips, clip_scenes=clip_scenes) if images else [[] for _ in scene_assets]
        image_rejections = list(getattr(resolve_shots, "rejections", []))
        music_file = local_music or (Path(music_asset.path) if music_asset else None)
        beats = audio_library.beat_times(music_file) if music_file else []
        log(f"beat sync: {len(beats)} beats" if beats else "beat sync: off (no steady pulse)")
        renderer = CinematicRenderer(script, narration, style, shots, handle=channel.get("handle", ""), beats=beats)
        visual_qa = run_visual_qa(script, narration, style, renderer, gemini, channel, beats,
                                  lambda banned: resolve_shots(script, narration, style, primary, cache, backup, extra_records,
                                                               stock, inspector=reset(inspector), clips=clips, banned=banned,
                                                               clip_scenes=clip_scenes))
        renderer = visual_qa.pop("renderer", renderer)
    else:
        renderer = FrameRenderer(script, narration, handle=channel.get("handle", ""), sources_note=on_screen_sources,
                                 images=image_specs)
    thumb_t = min(narration.scenes[0].start + 0.9, narration.scenes[0].end - 0.1)
    renderer.frame(thumb_t, captions=False).save(out / "cover.jpg", quality=92)
    cover_info = None
    if cinematic and images:  # designed cover for the profile grid, picked by the quality checker
        try:
            from .cover import make_cover

            cover_info = make_cover(renderer, script, out, gemini, channel.get("handle", ""))
        except Exception as exc:
            log(f"warning: cover design failed ({type(exc).__name__}: {exc}); using a video frame")

    if stills:
        for i, s in enumerate(narration.scenes):
            renderer.frame((s.start + s.end) / 2).save(out / f"still_{i + 1:02d}_{s.role}.jpg", quality=88)
        log(f"stills written to {out}")
        return {"out": str(out), "stills": True}

    sf.write(out / "voice.wav", narration.audio, narration.sample_rate)
    scene_starts, roles = [s.start for s in narration.scenes], [s.role for s in narration.scenes]
    sfx_track = None
    if cinematic:
        events = audio_library.sfx_events(scene_starts, roles, [sh.start for sh in renderer.shots], duration, style)
        sfx_track = audio_library.build_sfx_track(duration, events, script["id"], Path(out_root) / "_media_cache" / "audio", SFX_SR)
        log(f"sfx: {len(events)} library effects ({style['sfx_density']})" if sfx_track is not None else "sfx: synthesized")
    sf.write(out / "sfx.wav", sfx_track if sfx_track is not None else build_sfx_track(duration, scene_starts, roles), SFX_SR)
    music = local_music or (Path(music_asset.path) if music_asset else None)
    log(f"mixing audio (music: {music_asset.credit() if music_asset else (music.name if music else 'none')})")
    # style music_gain_db: -10 dB ~ the classic 0.30 bed; reference Reels sit around -7 dB
    music_volume = 0.30 * 10 ** ((style["music_gain_db"] + 10) / 20) if cinematic else 0.30
    mix_audio(out / "voice.wav", out / "sfx.wav", music, duration, out / "mix.wav", music_volume=music_volume)

    t1 = time.time()
    log(f"rendering {int(duration * FPS)} frames")
    encode(renderer, out / "mix.wav", out / "video.mp4")
    log(f"encode done in {time.time() - t1:.1f}s")

    errors, warnings, summary = validate_video(str(out / "video.mp4"))
    manifest = {
        "id": script["id"],
        "anime": script["anime"],
        "mode": script["mode"],
        "video": str(out / "video.mp4"),
        "cover": str(out / "cover.jpg"),
        "thumb_offset_ms": int(thumb_t * 1000),
        "designed_cover": cover_info,
        "caption": "\n\n".join(x for x in (
            full_caption(script),
            AI_DISCLOSURE if any(a and a.kind == "ai_image" for a in scene_assets) else "",
            OFFICIAL_ART_NOTE if any(a and a.kind == "official_image" for a in scene_assets) else "",
            credits_text([a for a in scene_assets if a and a.kind == "image"], music_asset)) if x),
        "knowledge_ids": ids,
        "sources": sources_note,  # kept for fact-checking; not shown on the video by default
        "unverified_knowledge_ids": unverified,
        "missing_knowledge_ids": missing,
        "publishable": not errors and not unverified and not missing and bool(ids),
        "music": music_asset.to_dict() if music_asset else (music.name if music else None),
        "images": [a.to_dict() if a else None for a in scene_assets],
        "voice": voice,
        "style": style,
        "image_rejections": image_rejections,
        "visual_qa": visual_qa,
        "shots": [len(row) for row in shots] if cinematic else None,
        "validation": {"errors": errors, "warnings": warnings, "summary": summary},
        "render_seconds": round(time.time() - t0, 1),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render an Instagram Reel from a script JSON")
    parser.add_argument("script")
    parser.add_argument("--out", default="output")
    parser.add_argument("--voice")
    parser.add_argument("--speed", type=float)
    parser.add_argument("--stills", action="store_true", help="only render preview stills")
    parser.add_argument("--no-images", action="store_true", help="typographic layout only")
    parser.add_argument("--no-music", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = render(args.script, args.out, args.voice, args.speed, args.stills,
                          images=not args.no_images, music=not args.no_music)
    except (ScriptError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    if manifest.get("validation", {}).get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
