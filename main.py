#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language Auto Publisher

Pipeline:
  ✅ TTS → Clean voice (no music)
  ✅ WhisperX → timestamps from clean voice (100% accurate)
  ✅ Render BG + Words overlay (with clean voice)
  ✅ Mix music + SFX separately
  ✅ Fix mixed audio duration to match clean voice
  ✅ Merge final video + fixed mixed audio
  ✅ Publish to Facebook + YouTube
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

from db import (
    init_db, is_render_done, mark_render_start,
    mark_render_done, mark_render_failed,
    save_script_meta, print_db_summary,
    has_ai_cache, get_ai_cache,
    save_ai_cache, clear_ai_cache, show_ai_cache,
    get_next_video_number, reset_published_for_lang,
    mark_video_published_for_lang,
    is_published_facebook, is_published_youtube,
    is_fully_published, make_cache_key, reset_used_videos,
)
from script_reader import (
    read_scripts, validate_scripts,
    process_tagged_content, print_scripts_summary,
)
from tags_parser   import print_tags_summary
from ai_enricher   import enrich_record, AIEnrichmentError
from tts           import synthesize_speech, VOICE_CONFIGS
from video_sources import fetch_videos_for_script
from srt           import generate_srt, generate_word_srt
from export        import export_all
from thumb_gen     import generate_thumbnail_html
from thumbnail     import render_thumbnails_batch
from sync          import (
    get_audio_duration, extract_transcript_from_audio, build_word_timeline,
)
from audio_manager import mix_voice_music_sfx
from facebook      import (
    publish_to_facebook, credentials_available as fb_credentials_available,
    check_credentials as fb_check_credentials,
)
from youtube       import (
    publish_to_youtube, credentials_available as yt_credentials_available,
    check_credentials as yt_check_credentials,
)

BASE_DIR      = Path(__file__).parent.resolve()
RENDER_SCRIPT = BASE_DIR / "remotion" / "render.mjs"
CONTENT_TYPE  = "motivational"
WPM           = 150.0
CLIP_DURATION = 3.0
SPEED_MULTIPLIER = {"ar": 1.15, "fr": 1.05, "en": 1.15}
DIMENSIONS = {"short": {"width": 1080, "height": 1920}, "long": {"width": 1920, "height": 1080}}
DURATION_LIMITS = {"short": {"min": 30, "max": 90}, "long": {"min": 120, "max": 900}}
MIN_VALID_AUDIO_S = 5.0
FFMPEG_TIMEOUT = 300
RENDER_TIMEOUT = 1800

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

def parse_args():
    p = argparse.ArgumentParser(description="🎬 Video Generator", formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("input_file", type=str, nargs="?", default=None)
    p.add_argument("--output-dir", type=str, default="output")
    p.add_argument("--video-number", type=str, default=None)
    p.add_argument("--auto-next", action="store_true")
    p.add_argument("--lang", type=str, default="ar", choices=["ar","fr","en"])
    p.add_argument("--content-mode", type=str, default="short", choices=["short","long"])
    p.add_argument("--formats", type=str, default="9x16")
    p.add_argument("--no-export", action="store_true")
    p.add_argument("--script-only", action="store_true")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--force-ai", action="store_true")
    p.add_argument("--publish-fb", action="store_true")
    p.add_argument("--publish-yt", action="store_true")
    p.add_argument("--no-publish", action="store_true")
    p.add_argument("--show-ai-cache", type=str, nargs="?", const="all", default=None)
    p.add_argument("--clear-ai-cache", type=str, default=None)
    p.add_argument("--reset-videos", action="store_true")
    return p.parse_args()

def _estimate_duration(text, content_mode="short"):
    limits = DURATION_LIMITS.get(content_mode, DURATION_LIMITS["short"])
    return max(limits["min"], min(limits["max"], int(len(text.split()) / (WPM / 60))))

def _should_publish_fb(args, content_mode):
    if args.no_publish or args.script_only or args.no_video: return False
    return args.publish_fb or fb_credentials_available()

def _should_publish_yt(args, lang):
    if args.no_publish or args.script_only or args.no_video: return False
    return args.publish_yt or yt_credentials_available(lang)

def _get_content_for_lang(record, lang):
    c = record.get(f"{lang}_content", "").strip()
    return c or record.get("content", "").strip()

def _reset_used_videos_cmd():
    count = reset_used_videos()
    log.info(f"  🗑️  Reset {count} used videos")
    return count

def _safe_unlink(path):
    try: Path(path).unlink(missing_ok=True)
    except: pass

def _run_ffmpeg(args, timeout=FFMPEG_TIMEOUT):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stderr
    except subprocess.TimeoutExpired: return False, "Timeout"
    except Exception as e: return False, str(e)

def _file_size_mb(path):
    try: return Path(path).stat().st_size / 1_048_576
    except: return 0.0

_TAG_RE = re.compile(r"\[[a-zA-Z_]+\]")
def _count_tags_in_content(content): return len(_TAG_RE.findall(content))

def _is_cache_stale(cached, content):
    tags = _count_tags_in_content(content)
    if tags <= 1: return False
    ct = cached.get("tagged") or []
    if not ct: log.info(f"  🔄 Cache stale: no tagged sentences ({tags} tags)"); return True
    if len(ct) < tags * 0.5: log.info(f"  🔄 Cache stale: {len(ct)} vs {tags}"); return True
    return False

def _inject_tags_into_aligned(aligned, tagged):
    if not aligned or not tagged: return aligned
    result = []
    for i, seg in enumerate(aligned):
        sc = dict(seg)
        sc["tag"] = tagged[i].get("final_tag", "information") if i < len(tagged) else "information"
        result.append(sc)
    log.info(f"  🏷️  Tags injected: {len(result)} segments")
    for i, s in enumerate(result):
        log.info(f"     [{i+1}] [{s.get('tag','information')}] {s.get('start',0):.2f}s → {s.get('end',0):.2f}s")
    return result

def _estimate_sentence_durations(sentences, total_duration):
    if not sentences: return []
    if total_duration <= 0: return [CLIP_DURATION] * len(sentences)
    wc = [max(1, len(s.split())) for s in sentences]
    tw = sum(wc)
    raw = [max(0.8, total_duration * c / tw) for c in wc]
    tr = sum(raw)
    if tr <= 0: return [CLIP_DURATION] * len(sentences)
    scale = total_duration / tr
    out = [round(d * scale, 3) for d in raw]
    diff = round(total_duration - sum(out), 3)
    if out: out[-1] = max(0.8, round(out[-1] + diff, 3))
    return out

def _normalize_keywords_row(row, index):
    defaults = ["dramatic close up face dark", "person staring camera shadow", "mysterious cinematic expression slow motion"]
    cleaned = [str(x).strip() for x in row if str(x).strip()] if isinstance(row, list) else []
    while len(cleaned) < 3: cleaned.append(defaults[len(cleaned) % 3])
    dedup, seen = [], set()
    for item in cleaned:
        k = item.lower().strip()
        if k and k not in seen: seen.add(k); dedup.append(item)
    while len(dedup) < 3: dedup.append(defaults[len(dedup) % 3])
    return dedup[:3]

def _build_clip_plan(script_data, ai_data, aligned, total_dur, content_mode="short"):
    sentences = script_data.get("sentences", [])
    vk = ai_data.get("visual_keywords", []) or []
    hk = (script_data.get("hook_keyword") or "").strip()
    if not sentences: return [], []
    ck, cd = [], []
    est = _estimate_sentence_durations(sentences, total_dur)
    if aligned and len(aligned) >= len(sentences):
        log.info(f"\n  🎞️  Clip plan from WhisperX ({len(sentences)} sentences) [{content_mode.upper()}]")
        for i in range(len(sentences)):
            cs = float(aligned[i].get("start", 0.0))
            ns = float(aligned[i+1].get("start", cs)) if i < len(sentences)-1 else total_dur
            dur = max(0.8, round(ns - cs, 3))
            row = _normalize_keywords_row(vk[i] if i < len(vk) else [], i)
            if i == 0 and hk and content_mode == "short":
                row = [hk] + [k for k in row if k.lower() != hk.lower()]
                row = (row + ["dramatic close up dark"])[:3]
            ck.append(row); cd.append(dur)
            log.info(f"     [{i+1}/{len(sentences)}] [{aligned[i].get('tag','info')}] {dur:.2f}s → {row[0]}")
        return ck, cd
    log.warning("\n  ⚠️  Using estimated durations fallback")
    for i in range(len(sentences)):
        row = _normalize_keywords_row(vk[i] if i < len(vk) else [], i)
        if i == 0 and hk and content_mode == "short":
            row = [hk] + [k for k in row if k.lower() != hk.lower()]
            row = (row + ["dramatic close up dark"])[:3]
        ck.append(row); cd.append(est[i] if i < len(est) else CLIP_DURATION)
    return ck, cd

def _trim_silence(audio_path, output_path):
    if not Path(audio_path).exists(): return audio_path
    log.info("  ✂️  Trimming leading silence...")
    ok, _ = _run_ffmpeg(["ffmpeg","-y","-i",audio_path,"-af","silenceremove=start_periods=1:start_duration=0.3:start_threshold=-40dB","-c:a","pcm_s16le",output_path])
    if not ok: return audio_path
    t = get_audio_duration(output_path)
    if t < 3.0: _safe_unlink(output_path); return audio_path
    log.info(f"  ✅ {get_audio_duration(audio_path):.1f}s → {t:.1f}s")
    return output_path

def _speed_up_audio(audio_path, speed, output_path):
    if abs(speed - 1.0) < 0.01 or not Path(audio_path).exists(): return audio_path
    log.info(f"  ⏩ Speeding up: {speed}x")
    ok, _ = _run_ffmpeg(["ffmpeg","-y","-i",audio_path,"-filter:a",f"atempo={speed}","-c:a","pcm_s16le",output_path])
    if not ok or not Path(output_path).exists(): return audio_path
    log.info(f"  ✅ Sped up: {get_audio_duration(output_path):.3f}s")
    return output_path

def produce_clean_voice(script_data, output_base, content_mode="short"):
    tagged_sentences = script_data["tagged_sentences"]
    lang = script_data.get("lang", "ar")
    vc = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    log.info(f"\n  🎙️  TTS ({lang.upper()}, voice={vc['voice_key']}, mode={content_mode.upper()})")
    synthesize_speech(tagged_sentences=tagged_sentences, output_path=f"{output_base}_voice", voice_key=vc["voice_key"], lang=lang)
    od = Path(output_base).parent; pf = Path(output_base).name
    wc = sorted(set(list(od.glob(f"{pf}_voice_*.wav")) + list(od.glob(f"{pf}_voice*.wav"))))
    rd = float(script_data["estimated_seconds"])
    wp = str(wc[0]) if wc else None
    if wp and Path(wp).exists():
        m = get_audio_duration(wp)
        if m >= MIN_VALID_AUDIO_S: rd = m; log.info(f"  📏 Raw: {rd:.3f}s")
    else: wp = None
    if wp:
        tr = _trim_silence(wp, f"{output_base}_voice_trimmed.wav")
        if tr != wp: wp = tr; d = get_audio_duration(wp);
        if d >= MIN_VALID_AUDIO_S: rd = d
    if content_mode == "short":
        sp = SPEED_MULTIPLIER.get(lang, 1.0)
        if wp and sp != 1.0:
            sped = _speed_up_audio(wp, sp, f"{output_base}_voice_fast.wav")
            if sped != wp: wp = sped; d = get_audio_duration(wp);
            if d >= MIN_VALID_AUDIO_S: rd = d
            log.info(f"  📏 After speed: {rd:.3f}s")
    cvp = Path(wp) if wp else Path(f"{output_base}_voice_0.wav")
    log.info(f"  ✅ Clean voice ready: {rd:.3f}s")
    return cvp, rd

def produce_mixed_audio(voice_path, script_data, output_base, aligned=None):
    lang = script_data.get("lang", "ar")
    rd = get_audio_duration(str(voice_path))
    mo = f"{output_base}_audio_mixed.aac"
    nc = max(1, int(rd / CLIP_DURATION))
    cdl = [rd / nc] * nc
    try:
        fa = mix_voice_music_sfx(voice_path=str(voice_path), content_type=CONTENT_TYPE,
            output_path=mo, clip_durations=cdl, sfx_type="swoosh", music_volume=0.12,
            seed=hash(script_data["title"]) % 10000, lang=lang,
            aligned=aligned or [], sentences=script_data.get("sentences", []),
            tagged=script_data["tagged_sentences"])
        d = get_audio_duration(str(fa))
        log.info(f"  ✅ Mixed audio ready: {d:.3f}s")
        return Path(fa)
    except Exception as e:
        log.warning(f"  ⚠️  Mix error: {e} — using clean voice")
        return voice_path

def run_whisperx(clean_voice_path, out_base, lang, script_sentences=None):
    log.info(f"\n  🎤 WhisperX: {clean_voice_path.name}")
    wi = f"{out_base}_whisper_input.wav"
    ok, _ = _run_ffmpeg(["ffmpeg","-y","-i",str(clean_voice_path),"-acodec","pcm_s16le","-ar","16000","-ac","1",wi])
    if not ok: wi = str(clean_voice_path)
    tr = extract_transcript_from_audio(wi, lang=lang)
    _safe_unlink(wi)
    if not tr["success"]: log.warning("  ⚠️  WhisperX failed"); return [], []
    aligned = tr["aligned"]; sentences = tr["sentences"]
    if script_sentences:
        wt = [w for seg in tr["aligned"] for w in seg.get("words", [])]
        tsw = sum(len(s.split()) for s in script_sentences)
        if wt and len(wt) >= 5 and tsw > 0:
            dr = abs(len(wt) - tsw) / tsw
            if dr <= 0.30:
                try:
                    _, rebuilt = build_word_timeline(script_sentences, wt, tr["total_duration"])
                    if rebuilt and len(rebuilt) == len(script_sentences):
                        aligned = rebuilt; sentences = list(script_sentences)
                        log.info(f"  ✅ Re-mapped: {len(sentences)} sentences")
                except Exception as e: log.warning(f"  ⚠️  Remap skipped: {e}")
            else: log.warning(f"  ⚠️  Remap skipped — words: {len(wt)} vs script: {tsw}")
    tw = sum(len(s.get("words", [])) for s in aligned)
    log.info(f"  ✅ WhisperX: {len(sentences)} sentences, {tw} words")
    generate_srt(aligned, f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")
    return aligned, sentences

def _build_manifest(script_data, audio_path, video_paths, real_dur, clip_durations, aligned, content_mode, mode, has_hook=False):
    ac = sum(clip_durations) / len(clip_durations) if clip_durations else CLIP_DURATION
    return {"title": script_data["title"], "display_title": script_data.get("display_title", script_data["title"]),
        "emoji_left": script_data.get("emoji_left","🔥"), "emoji_right": script_data.get("emoji_right","💥"),
        "sentences": script_data["sentences"], "audio": str(Path(str(audio_path)).resolve()),
        "videos": [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s": real_dur, "lang": script_data.get("lang","ar"),
        "content_type": CONTENT_TYPE, "content_mode": content_mode,
        "power_words": script_data.get("power_words",[]), "accent_colors": script_data.get("accent_colors",[]),
        "analysis": script_data.get("analysis",{}), "clip_duration": ac, "clip_durations": clip_durations,
        "has_hook": has_hook, "hook_keyword": script_data.get("hook_keyword",""),
        "custom_hook": script_data.get("custom_hook",""), "aligned": aligned, "mode": mode}

def _run_remotion_render(manifest_path, output_path):
    if not RENDER_SCRIPT.exists(): raise FileNotFoundError(f"render.mjs not found: {RENDER_SCRIPT}")
    try:
        r = subprocess.run(["node",str(RENDER_SCRIPT.resolve()),str(manifest_path),str(output_path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired: raise RuntimeError(f"Render timeout ({RENDER_TIMEOUT}s)")
    if r.returncode != 0: raise RuntimeError(f"Render failed:\n{r.stdout[-600:]}")

def produce_bg_video(video_paths, audio_path, real_dur, out_base, script_data, has_hook, clip_durations, content_mode="short"):
    bm = "bg_only" if content_mode == "short" else "long_bg_only"
    sf = f"_{content_mode}"
    m = _build_manifest(script_data, audio_path, video_paths, real_dur, clip_durations, [], content_mode, bm, has_hook)
    mp = Path(f"{out_base}{sf}_bg_manifest.json").resolve()
    mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    op = Path(f"{out_base}{sf}_bg.mp4").resolve()
    log.info(f"\n  🎬 Producing background video [{content_mode.upper()}]...")
    _run_remotion_render(mp, op)
    log.info(f"  ✅ BG video [{content_mode.upper()}]: {_file_size_mb(op):.1f} MB")
    return op

def render_words_overlay(bg_video, audio_path, aligned, sentences, script_data, out_base, content_mode="short"):
    ad = get_audio_duration(str(audio_path))
    wm = "words_only" if content_mode == "short" else "long_words_only"
    sf = f"_{content_mode}"
    m = _build_manifest({**script_data, "sentences": sentences}, audio_path, [bg_video], ad, [ad], aligned, content_mode, wm, script_data.get("has_hook", False))
    mp = Path(f"{out_base}{sf}_words_manifest.json").resolve()
    mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    op = Path(f"{out_base}{sf}_final.mp4").resolve()
    log.info(f"\n  🔧 Rendering words overlay [{content_mode.upper()}]...")
    _run_remotion_render(mp, op)
    log.info(f"  🎉 Final [{content_mode.upper()}]: {op.name} ({_file_size_mb(op):.1f} MB)")
    return op

def merge_video_audio(video_path, audio_path, output_path):
    log.info(f"\n  🔊 Merging final video + mixed audio...")
    ad = get_audio_duration(str(audio_path))
    vd = get_audio_duration(str(video_path))
    log.info(f"     Video: {vd:.1f}s | Audio: {ad:.1f}s")
    ok, err = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ])
    if not ok:
        log.warning(f"  ⚠️  Merge failed: {err[:100]}")
        return video_path
    log.info(f"  ✅ Merged: {output_path.name} ({_file_size_mb(output_path):.1f} MB)")
    return output_path

def produce_fb_vertical_version(script_data, audio_path, aligned, video_paths, clip_durations, out_base):
    try:
        log.info("\n  📱 Rendering Facebook vertical version (9:16)...")
        rd = get_audio_duration(str(audio_path))
        vdf = str(Path(out_base).parent / f"{Path(out_base).name}_fb_videos")
        fk = [[str(p.stem)] if hasattr(p,"stem") else ["cinematic dark person"] for p in video_paths]
        fvp = fetch_videos_for_script(keywords_per_sentence=fk, clip_durations=clip_durations, output_dir=vdf, aligned=aligned, content_mode="short")
        bg = produce_bg_video(fvp, audio_path, rd, f"{out_base}_fb", script_data, False, clip_durations, "short")
        ff = render_words_overlay(bg, audio_path, aligned, script_data["sentences"], script_data, f"{out_base}_fb", "short")
        log.info(f"  ✅ Facebook vertical ready: {ff.name}")
        return ff
    except Exception as e: log.error(f"  ⚠️  FB vertical failed: {e}"); traceback.print_exc(); return None

def get_or_create_ai_data(record, lang, tagged, content_mode="short", force_ai=False, content=""):
    vn = str(record["number"]); title = record.get("title","")
    ck = make_cache_key(vn, lang, content_mode)
    if not force_ai and has_ai_cache(ck):
        cached = get_ai_cache(ck)
        if cached and cached.get("hook_keyword"):
            if content and _is_cache_stale(cached, content):
                log.info(f"\n  🔄 Auto-invalidating stale cache for #{vn} [{content_mode.upper()}]")
                clear_ai_cache(ck)
            else: log.info(f"\n  ♻️  Using cached AI for #{vn} [{content_mode.upper()}]"); return cached
    ctu = content or _get_content_for_lang(record, lang)
    if not ctu: raise AIEnrichmentError(f"No content for #{vn} ({lang.upper()})")
    enriched = enrich_record(record={"number":vn,"title":title,"content":ctu}, lang=lang, tagged=tagged, verbose=True)
    save_ai_cache(cache_key=ck, title=title, lang=lang, enriched=enriched, content_mode=content_mode)
    log.info(f"  💾 AI cached for #{vn} [{content_mode.upper()}]"); return enriched

def _build_script_data(record, lang, ai_data, tagged, content_mode="short"):
    if not tagged: return None
    sc = [s["text"] for s in tagged]; fs = " ".join(sc)
    at = ai_data.get("attractive_title") or {}
    pw = ai_data.get("power_words", [])
    if isinstance(pw, dict): pw = pw.get(lang) or pw.get("ar") or pw.get("en") or []
    em = ai_data.get("analysis",{}).get("primary_emotion","")
    bs = {"fear":"cinematic","sadness":"cinematic","awe":"blur"}.get(em,"video")
    return {"title":record["title"],"display_title":at.get("title") or record["title"],
        "emoji_left":at.get("emoji_left","🔥"),"emoji_right":at.get("emoji_right","💥"),
        "hook":sc[0] if sc else "","full_script":fs,"sentences":sc,"tagged_sentences":tagged,
        "estimated_seconds":_estimate_duration(fs,content_mode),"word_count":len(fs.split()),
        "lang":lang,"content_mode":content_mode,"content_type":CONTENT_TYPE,"power_words":pw,
        "accent_colors":ai_data.get("accent_colors",[]),"visual_keywords":ai_data.get("visual_keywords",[]),
        "analysis":ai_data.get("analysis",{}),"hook_keyword":ai_data.get("hook_keyword",""),
        "custom_hook":ai_data.get("custom_hook",""),"bg_style":bs,
        "has_hook":bool(ai_data.get("hook_keyword","") and content_mode=="short")}

def _rebuild_text_with_tag(tagged):
    for s in tagged:
        ft = s.get("final_tag"); t = s.get("text","")
        s["text_with_tag"] = f"[{ft}] {t}" if ft else t
    return tagged

def _do_publish(video_path, record, ai_data, lang, video_number, content_mode,
    should_publish_fb, should_publish_yt, fb_video_path="", yt_video_path=""):
    if not Path(video_path).exists(): log.error("  ❌ Publish skipped: video not found"); return
    sd = ai_data.get("street_description",""); title = record.get("title","")
    fp = fb_video_path or video_path; yp = yt_video_path or video_path
    if should_publish_fb:
        if is_published_facebook(video_number,lang,content_mode): log.info("  ⏭️  Facebook: already published")
        else:
            try:
                publish_to_facebook(video_path=fp,record=record,lang=lang,as_reel=(content_mode=="short"),ai_caption=sd or title,content_mode=content_mode)
                mark_video_published_for_lang(video_number,lang,"facebook",content_mode); log.info("  📘 Facebook: published ✅")
            except Exception as e: log.error(f"  ❌ Facebook publish failed: {e}")
    if should_publish_yt:
        if is_published_youtube(video_number,lang,content_mode): log.info(f"  ⏭️  YouTube: already published [{content_mode}]")
        else:
            try:
                publish_to_youtube(video_path=yp,record=record,lang=lang,street_description=sd,content_mode=content_mode)
                mark_video_published_for_lang(video_number,lang,"youtube",content_mode); log.info(f"  📺 YouTube: published ✅ [{content_mode}]")
            except Exception as e: log.error(f"  ❌ YouTube publish failed: {e}")

def process_video(record, args, out_dir, should_publish_fb, should_publish_yt, content_mode="short"):
    num = str(record["number"]); title = record["title"]; lang = args.lang; ml = content_mode.upper()
    result = {"video_paths":[],"hook_keyword":title}
    log.info(f"\n{'═'*65}")
    log.info(f"  🎬  Video #{num} ({lang.upper()}) [{ml}]:  {title}")
    log.info(f"{'═'*65}")
    out_base = str(Path(out_dir).resolve() / f"video_{num}_{lang}_{content_mode}")

    content = _get_content_for_lang(record, lang)
    if not content: log.error(f"  ❌ No content for #{num}"); return result
    log.info(f"\n  🏷️  Parsing {lang.upper()} tags [{ml}]")
    tagged = process_tagged_content(content, lang=lang)
    if not tagged: log.error(f"  ❌ No tagged content for #{num}"); return result
    log.info(f"  ✅ Parsed: {len(tagged)} sentences")

    try:
        ai_data = get_or_create_ai_data(record=record,lang=lang,tagged=tagged,content_mode=content_mode,force_ai=args.force_ai,content=content)
    except AIEnrichmentError as e: log.error(f"\n  ⛔ AI enrichment failed: {e}"); return result
    tagged = _rebuild_text_with_tag(ai_data.get("tagged") or tagged)
    hook_keyword = ai_data.get("hook_keyword","") or title; result["hook_keyword"] = hook_keyword

    script_data = _build_script_data(record, lang, ai_data, tagged, content_mode)
    if not script_data: log.error("  ❌ Cannot build script data"); return result
    log.info(f"  📊 Final sentences: {len(script_data['sentences'])} [{ml}]")
    sd = ai_data.get("street_description","")
    if sd: log.info(f"  📝 Street Description: {len(sd)} chars")
    save_script_meta(video_number=num,title=title,lang=lang,sentences=len(tagged),words=script_data["word_count"],content_mode=content_mode)

    if args.script_only: print_tags_summary(tagged, lang=lang); return result
    if args.no_video:
        log.info(f"\n  🎵 Audio only [{ml}]")
        try: produce_clean_voice(script_data, out_base, content_mode)
        except Exception as e: log.error(f"  ❌ Audio error: {e}")
        return result

    mark_render_start(num, lang, content_mode)

    try:
        # STEP A: صوت نظيف (بدون موسيقى)
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP A: Clean voice [{ml}]")
        clean_voice_path, real_dur = produce_clean_voice(script_data=script_data, output_base=out_base, content_mode=content_mode)

        # STEP B: WhisperX (يحلل الصوت النظيف = تطابق 100%)
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP B: WhisperX [{ml}] (clean voice: {real_dur:.1f}s)")
        aligned, whisper_sentences = run_whisperx(clean_voice_path=clean_voice_path, out_base=out_base, lang=lang, script_sentences=script_data["sentences"])
        if not whisper_sentences: whisper_sentences = script_data["sentences"]
        aligned = _inject_tags_into_aligned(aligned, tagged)

        # STEP C: جلب فيديوهات
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP C: Clip plan + videos [{ml}]")
        clip_keywords, clip_durations = _build_clip_plan(script_data=script_data, ai_data=ai_data, aligned=aligned, total_dur=real_dur, content_mode=content_mode)
        if not clip_keywords: raise RuntimeError("Could not build clip plan")
        vid_dir = str(Path(out_dir).resolve() / f"videos_{num}_{lang}_{content_mode}")
        video_paths = fetch_videos_for_script(keywords_per_sentence=clip_keywords, clip_durations=clip_durations, output_dir=vid_dir, aligned=aligned, content_mode=content_mode)
        result["video_paths"] = [str(p) for p in video_paths]

        # STEP D: Background video (صوت نظيف)
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP D: Background video [{ml}]")
        bg_video = produce_bg_video(video_paths=video_paths, audio_path=clean_voice_path, real_dur=real_dur, out_base=out_base, script_data=script_data, has_hook=script_data.get("has_hook",False), clip_durations=clip_durations, content_mode=content_mode)

        # STEP E: Words overlay (صوت نظيف + timestamps دقيقة)
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP E: Words overlay [{ml}]")
        video_with_words = render_words_overlay(bg_video=bg_video, audio_path=clean_voice_path, aligned=aligned, sentences=whisper_sentences, script_data=script_data, out_base=out_base, content_mode=content_mode)

        # STEP F: Mix music + SFX (منفصل) + ضبط المدة
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP F: Mix music + SFX [{ml}]")
        mixed_audio = produce_mixed_audio(voice_path=clean_voice_path, script_data=script_data, output_base=out_base, aligned=aligned)

        # ✅ ضبط مدة الصوت الممزوج لتتطابق مع النظيف
        mixed_dur = get_audio_duration(str(mixed_audio))
        clean_dur = get_audio_duration(str(clean_voice_path))
        log.info(f"  📏 Clean: {clean_dur:.3f}s | Mixed: {mixed_dur:.3f}s")

        if abs(mixed_dur - clean_dur) > 0.3:
            fixed_audio = f"{out_base}_audio_fixed.aac"
            log.info(f"  🔧 Fixing duration: {mixed_dur:.1f}s → {clean_dur:.1f}s")
            ok, _ = _run_ffmpeg(["ffmpeg","-y","-i",str(mixed_audio),"-t",f"{clean_dur:.3f}","-c:a","aac","-b:a","192k",fixed_audio])
            if ok and Path(fixed_audio).exists():
                mixed_audio = Path(fixed_audio)
                log.info(f"  ✅ Fixed: {get_audio_duration(str(mixed_audio)):.3f}s")
            else: log.warning("  ⚠️  Fix failed, using original")

        # STEP G: دمج الفيديو + الصوت الممزوج
        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP G: Merge video + mixed audio [{ml}]")
        suffix = f"_{content_mode}"
        final_output = Path(f"{out_base}{suffix}_published.mp4").resolve()
        final_video = merge_video_audio(video_path=video_with_words, audio_path=mixed_audio, output_path=final_output)

        # STEP H: Facebook vertical (Long فقط)
        fb_vertical = None
        if content_mode == "long" and should_publish_fb:
            log.info(f"\n  {'─'*55}")
            log.info("  ✅ STEP H: Facebook vertical version")
            fb_vertical = produce_fb_vertical_version(script_data=script_data, audio_path=clean_voice_path, aligned=aligned, video_paths=video_paths, clip_durations=clip_durations, out_base=out_base)

        export_formats = [] if args.no_export else [f.strip() for f in args.formats.split(",") if f.strip()]
        if export_formats and content_mode == "short": export_all(str(final_video), out_base, export_formats)

        fb_path = str(fb_vertical) if fb_vertical else str(final_video)
        yt_path = str(final_video)
        mark_render_done(num, lang, str(final_video), real_dur, content_mode, fb_path=fb_path, yt_path=yt_path)

        log.info(f"\n  {'─'*55}")
        log.info(f"  ✅ STEP I: Publishing [{ml}]")
        _do_publish(video_path=str(final_video), record=record, ai_data=ai_data, lang=lang, video_number=num, content_mode=content_mode, should_publish_fb=should_publish_fb, should_publish_yt=should_publish_yt, fb_video_path=fb_path, yt_video_path=yt_path)

        log.info(f"\n  ✅ Video #{num} ({lang.upper()}) [{ml}] → {final_video.name} ({_file_size_mb(final_video):.1f} MB)")

    except Exception as e:
        mark_render_failed(num, lang, str(e), content_mode)
        log.error(f"\n  ❌ Failed [{ml}]: {e}"); traceback.print_exc()

    return result

def _generate_thumbnails(valid, video_results, args, content_mode):
    tq = []
    for r in valid:
        n = r["number"]; ob = str(Path(args.output_dir).resolve() / f"video_{n}_{args.lang}_{content_mode}")
        hp, pp = f"{ob}_thumbnail.html", f"{ob}_thumbnail.png"
        if Path(pp).exists(): continue
        try:
            vr = video_results.get(str(n),{}); hk = vr.get("hook_keyword",r["title"]); vp = vr.get("video_paths",[])
            generate_thumbnail_html(title=r["title"],lang=args.lang,output_path=hp,keyword=hk,video_paths=vp,content_mode=content_mode)
            tq.append((hp, pp))
        except Exception as e: log.warning(f"  ⚠️  Thumbnail error: {e}")
    if tq:
        log.info(f"\n🖼️  Rendering {len(tq)} thumbnail(s) [{content_mode.upper()}]")
        try: render_thumbnails_batch(items=tq, content_mode=content_mode)
        except Exception as e: log.error(f"  ⚠️  Thumbnail render error: {e}")

def _try_publish_existing(record, args, content_mode, will_publish_fb, will_publish_yt):
    num = record["number"]; lang = args.lang; sf = f"_{content_mode}"
    ob = str(Path(args.output_dir).resolve() / f"video_{num}_{lang}_{content_mode}")
    yp = f"{ob}{sf}_published.mp4"
    if not Path(yp).exists(): yp = f"{ob}{sf}_final.mp4"
    if is_fully_published(num,lang,content_mode): log.info(f"  ⏭️  #{num} [{content_mode.upper()}] already published"); return
    fd = is_published_facebook(num,lang,content_mode); yd = is_published_youtube(num,lang,content_mode)
    ad = get_ai_cache(make_cache_key(str(num),lang,content_mode)) or {}
    fp = f"{ob}_fb_short_final.mp4"
    if not Path(fp).exists(): fp = yp
    _do_publish(video_path=yp,record=record,ai_data=ad,lang=lang,video_number=str(num),content_mode=content_mode,should_publish_fb=will_publish_fb and not fd,should_publish_yt=will_publish_yt and not yd,fb_video_path=fp,yt_video_path=yp)

def _handle_management_commands(args):
    if args.show_ai_cache is not None: show_ai_cache(args.show_ai_cache if args.show_ai_cache != "all" else None); return True
    if args.clear_ai_cache is not None:
        c = clear_ai_cache() if args.clear_ai_cache == "all" else clear_ai_cache(args.clear_ai_cache)
        log.info(f"  🗑️  Cleared {c} entries"); return True
    if args.reset_videos: _reset_used_videos_cmd();
    if args.reset_videos and not args.input_file: return True
    return False

def main():
    args = parse_args(); cm = args.content_mode; init_db()
    if _handle_management_commands(args): return
    if not args.input_file: log.error("❌ input_file is required"); sys.exit(1)
    lang = args.lang; wpf = _should_publish_fb(args,cm); wpy = _should_publish_yt(args,lang)
    log.info(f"\n{'═'*62}"); log.info(f"  🚀  Video Generator — {lang.upper()} [{cm.upper()}]"); log.info(f"{'═'*62}")
    log.info(f"  Input        : {args.input_file}"); log.info(f"  Language     : {lang.upper()}")
    log.info(f"  Content Mode : {cm.upper()}"); log.info(f"  Output       : {args.output_dir}")
    log.info(f"  Facebook     : {'✅' if wpf else '❌'}"); log.info(f"  YouTube      : {'✅' if wpy else '❌'}")
    log.info(""); print_db_summary()
    if wpf:
        log.info("\n📘 Checking Facebook credentials...")
        if not fb_check_credentials(): log.warning("  ⚠️  FB invalid — disabled"); wpf = False
    if wpy:
        log.info(f"\n📺 Checking YouTube credentials ({lang.upper()})...")
        if not yt_check_credentials(lang): log.warning("  ⚠️  YT invalid — disabled"); wpy = False
    log.info("\n📖  Reading scripts...")
    try: all_scripts = read_scripts(args.input_file)
    except Exception as e: log.error(f"❌  Cannot read: {e}"); sys.exit(1)
    valid, errors = validate_scripts(all_scripts)
    for e in errors: log.warning(e)
    if not valid: log.error("❌  No valid scripts"); sys.exit(1)
    print_scripts_summary(valid)
    if args.auto_next:
        available = [str(s["number"]) for s in valid]
        nn = get_next_video_number(lang, available, cm)
        if nn is None: log.info(f"\n  🔄 Looping [{cm.upper()}]"); reset_published_for_lang(lang,cm); nn = str(valid[0]["number"])
        log.info(f"\n  🎯 Auto-next: #{nn} [{cm.upper()}]"); valid = [s for s in valid if str(s["number"]) == nn]
    elif args.video_number:
        valid = [s for s in valid if str(s["number"]) == str(args.video_number)]
        if not valid: log.error(f"❌  Video #{args.video_number} not found"); sys.exit(1)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    success = failed = 0; vr = {}
    for i, record in enumerate(valid, 1):
        log.info(f"\n[{i}/{len(valid)}]")
        if not args.force and is_render_done(record["number"],lang,cm):
            _try_publish_existing(record=record,args=args,content_mode=cm,will_publish_fb=wpf,will_publish_yt=wpy); continue
        try:
            r = process_video(record=record,args=args,out_dir=args.output_dir,should_publish_fb=wpf,should_publish_yt=wpy,content_mode=cm)
            vr[str(record["number"])] = r; success += 1
        except KeyboardInterrupt: log.warning("\n⛔  Interrupted"); break
        except Exception as e: log.error(f"  ❌  Error: {e}"); traceback.print_exc(); failed += 1
    _generate_thumbnails(valid=valid,video_results=vr,args=args,content_mode=cm)
    log.info(f"\n{'═'*62}"); log.info(f"  ✅  Done ({lang.upper()}) [{cm.upper()}] — {success} success | {failed} failed")
    print_db_summary(); log.info(f"{'═'*62}\n")

if __name__ == "__main__": main()
