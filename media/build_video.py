"""
Render tutorial.mp4 from the captured screens and the script.

    python media/capture.py       # once; it starts its own copy of the app
    python media/build_video.py

There is no voice track. A synthetic narrator makes a careful product sound
cheap, so the words are on screen instead and the only audio is an original
generated bed (see media/music.py) sitting well under them.

That changes the timing model. With narration, each scene lasted as long as its
spoken line; now each subtitle lasts as long as it takes to *read*, and a scene
lasts as long as its subtitles. Nothing is truncated to fit - a long point is
split into consecutive subtitles instead, because the text is now the whole
channel and an ellipsis would simply lose information.

Frames are composed with Pillow and piped into ffmpeg as raw RGB, so nothing
is written to disk except the finished file and an .srt beside it.
"""

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

import music                                           # noqa: E402
from script import SCENES                              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "media" / "shots"
WORK = ROOT / "media" / "work"
OUT = ROOT / "media" / "tutorial.mp4"
SRT = ROOT / "media" / "tutorial.srt"

W, H = 1920, 1080
FPS = 30

# the app's own colours (DESIGN.md): cloth cover, turmeric gold, ruled paper
COVER = (0x74, 0x19, 0x1F)
COVER_DEEP = (0x58, 0x12, 0x1A)
GOLD = (0xE9, 0xC4, 0x6A)
GOLD_LINE = (0xBA, 0x80, 0x4C)      # the cover's gold edge, 60% gold on maroon
GOLD_INK = (0x9A, 0x6A, 0x00)
PAPER = (0xFF, 0xFF, 0xFF)
PALE = (0xF1, 0xDF, 0xE0)           # quieter white on the cover
CANVAS = (0xF5, 0xF6, 0xF8)         # blotter wash around the screen
RULE_STRONG = (0xC3, 0xCD, 0xD8)
MARGIN_RULE = (0xE6, 0xAE, 0xB3)
INK = (0x1C, 0x1E, 0x23)
INK_2 = (0x45, 0x4A, 0x54)
INK_3 = (0x62, 0x67, 0x73)

# Segoe UI, the face the app itself is drawn in on Windows.
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

CAPTION_H = 214
MARGIN = 54                                     # round the screen
STAGE = (W - MARGIN * 2, H - CAPTION_H - MARGIN - 30)
TEXT_X = (W - round(STAGE[1] * 16 / 9)) // 2    # captions line up with the screen
XFADE = 10                  # frames of cross-fade into each scene

# Reading speed. 0.34s a word is a shade under 180 wpm, which is unhurried for
# on-screen text; the floor keeps a three-word line from flashing past.
SECONDS_PER_WORD = 0.34
MIN_SUBTITLE = 2.9
LEAD_IN = 1.0
CARD_AIR = 1.6              # extra hold on title and section cards


def load_fonts():
    def font(file, size):
        return ImageFont.truetype(str(FONTS / file), size)

    return {
        "title": font("segoeuib.ttf", 60),         # the wordmark, tracked caps
        "outro": font("segoeuib.ttf", 84),
        "sub": font("segoeui.ttf", 38),
        "sect_n": font("segoeuib.ttf", 26),
        "sect": font("segoeuib.ttf", 68),
        "label": font("segoeuib.ttf", 22),
        "caption": font("segoeui.ttf", 35),
        "marker": font("seguisb.ttf", 19),
    }


# ------------------------------------------------------------------ subtitles

def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def split_sentences(text):
    """Break on sentence ends, keeping the punctuation."""
    out, current = [], ""
    for chunk in text.replace("; ", ". ").split(" "):
        current = f"{current} {chunk}".strip()
        if chunk.endswith((".", ":", "?")) and len(current.split()) >= 4:
            out.append(current)
            current = ""
    if current:
        if out and len(current.split()) < 4:
            out[-1] = f"{out[-1]} {current}"        # a stub joins the one before
        else:
            out.append(current)
    return out


def subtitles_for(text, fonts, max_lines=2):
    """Consecutive subtitles, each at most `max_lines` lines when wrapped.

    Sentences are kept whole where they fit, because a subtitle that breaks
    mid-clause is harder to read than one that runs a beat longer.
    """
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    width = W - TEXT_X * 2
    cards, pending = [], ""

    def fits(candidate):
        return len(wrap(draw, candidate, fonts["caption"], width)) <= max_lines

    for sentence in split_sentences(text):
        joined = f"{pending} {sentence}".strip()
        if fits(joined):
            pending = joined
            continue
        if pending:
            cards.append(pending)
        if fits(sentence):
            pending = sentence
            continue
        # one sentence too long for a card: break it on words
        words, run = sentence.split(), ""
        for word in words:
            trial = f"{run} {word}".strip()
            if fits(trial):
                run = trial
            else:
                cards.append(run)
                run = word
        pending = run
    if pending:
        cards.append(pending)

    return [(card, wrap(draw, card, fonts["caption"], width)) for card in cards]


def reading_time(text):
    return max(MIN_SUBTITLE, LEAD_IN + len(text.split()) * SECONDS_PER_WORD)


# ---------------------------------------------------------------------- paint

def tracked(draw, xy, text, font, fill, spacing=0):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def tracked_width(draw, text, font, spacing=0):
    return sum(draw.textlength(c, font=font) + spacing for c in text)


def base_frame(colour=CANVAS):
    return Image.new("RGB", (W, H), colour)


def progress_rule(draw, done):
    """A gold line showing position. A silent video needs somewhere to be."""
    draw.rectangle([0, H - 3, W, H], fill=COVER_DEEP)
    if done > 0:
        draw.rectangle([0, H - 3, int(W * done), H], fill=GOLD)


def title_frame(fonts, title, sub, lines=(), key="title", done=0.0):
    """The cover: the wordmark, and the opening words under it.

    The text has to live on the card rather than being dropped: with no
    narrator it is the only place the viewer learns what the thing does, and a
    wordmark alone cannot justify twenty seconds of screen time.
    """
    img = base_frame(COVER)
    d = ImageDraw.Draw(img)
    f = fonts[key]
    ty = H // 2 - 210 if lines else H // 2 - 118
    if key == "title":                  # set the way the app sets its wordmark
        mark = title.upper()
        tw = tracked_width(d, mark, f, 10) - 10
        tracked(d, ((W - tw) / 2, ty), mark, f, GOLD, spacing=10)
    else:
        tw = d.textlength(title, font=f)
        d.text(((W - tw) / 2, ty), title, font=f, fill=PAPER)
    rule_w = 132
    rule_y = ty + f.size + 52
    d.line([(W - rule_w) / 2, rule_y, (W + rule_w) / 2, rule_y],
           fill=GOLD_LINE, width=2)
    sw = d.textlength(sub, font=fonts["sub"])
    d.text(((W - sw) / 2, rule_y + 32), sub, font=fonts["sub"], fill=PAPER)

    y = rule_y + 32 + fonts["sub"].size + 66
    for line in lines or ():
        lw = d.textlength(line, font=fonts["caption"])
        d.text(((W - lw) / 2, y), line, font=fonts["caption"], fill=PALE)
        y += 46
    progress_rule(d, done)
    return img


def section_frame(fonts, number, name, blurb_lines, done=0.0):
    """A fresh page of the book: the cover's edge on top, the rose margin rule."""
    img = base_frame(PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 13], fill=COVER)
    d.line([0, 14, W, 14], fill=GOLD_LINE, width=1)
    for x in (TEXT_X - 46, TEXT_X - 41):                # the margin's double rule
        d.line([x, 15, x, H], fill=MARGIN_RULE, width=2)
    y = H // 2 - 130
    tracked(d, (TEXT_X, y), number, fonts["sect_n"], GOLD_INK, spacing=3)
    d.line([TEXT_X, y + 52, TEXT_X + 88, y + 52], fill=GOLD, width=2)
    d.text((TEXT_X, y + 82), name, font=fonts["sect"], fill=INK)
    ty = y + 82 + fonts["sect"].size + 44
    for line in blurb_lines:
        d.text((TEXT_X, ty), line, font=fonts["caption"], fill=INK_2)
        ty += 46
    progress_rule(d, done)
    return img


def fit_shot(path, focus=None):
    img = Image.open(path).convert("RGB")
    if focus:
        l, t, r, b = focus
        img = img.crop((int(l * img.width), int(t * img.height),
                        int(r * img.width), int(b * img.height)))
    return img


def screen_frame(fonts, shot, label, lines, progress, section, done,
                 focus=None, cache={}):
    img = base_frame()
    d = ImageDraw.Draw(img)

    key = (shot, focus)
    if key not in cache:
        cache.clear()                       # only ever need the current shot
        cache[key] = fit_shot(shot, focus)
    src = cache[key]

    # A slow push inside a window that holds still: the page draws nearer.
    scale = min(STAGE[0] / src.width, STAGE[1] / src.height)
    fw, fh = int(src.width * scale), int(src.height * scale)
    zoom = 1.0 + 0.035 * progress
    cw, ch = src.width / zoom, src.height / zoom
    cx, cy = (src.width - cw) / 2, (src.height - ch) / 2
    frame = src.resize((fw, fh), Image.LANCZOS, box=(cx, cy, cx + cw, cy + ch))
    x0 = MARGIN + (STAGE[0] - fw) // 2
    y0 = MARGIN + (STAGE[1] - fh) // 2
    img.paste(frame, (x0, y0))
    d.rectangle([x0 - 1, y0 - 1, x0 + fw, y0 + fh], outline=RULE_STRONG, width=1)

    # where we are, top right, for a video with nobody talking
    if section:
        mark = section.upper()
        mw = tracked_width(d, mark, fonts["marker"], 2.0) - 2.0
        tracked(d, (W - TEXT_X - mw, 20), mark, fonts["marker"], INK_3, spacing=2.0)

    top = H - CAPTION_H
    d.rectangle([0, top, W, H], fill=COVER)
    d.line([0, top, W, top], fill=GOLD_LINE, width=2)
    tracked(d, (TEXT_X, top + 32), label.upper(), fonts["label"], GOLD, spacing=2.2)

    y = top + 76
    for line in lines:
        d.text((TEXT_X, y), line, font=fonts["caption"], fill=PAPER)
        y += 46
    progress_rule(d, done)
    return img


# --------------------------------------------------------------------- timing

def plan():
    """Work out every scene's subtitles and duration before rendering."""
    fonts = load_fonts()
    timeline, clock = [], 0.0
    section = None

    for scene in SCENES:
        kind = scene["kind"]
        if kind == "section":
            section = f"{scene['n']} {scene['name']}"

        cards = subtitles_for(scene["say"], fonts)
        seconds = sum(reading_time(t) for t, _ in cards)
        if kind != "screen":
            seconds += CARD_AIR             # title and section cards hold a beat

        timeline.append({"scene": scene, "cards": cards, "seconds": seconds,
                         "start": clock, "section": section})
        clock += seconds
    return fonts, timeline, clock


def write_srt(timeline, path=SRT):
    """A sidecar, so the text is selectable and usable on any platform."""
    def stamp(seconds):
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    out, index = [], 1
    for entry in timeline:
        at = entry["start"]
        scene = entry["scene"]
        # A card's heading is on screen from its first frame, so the sidecar
        # carries it on the first subtitle. Giving it a slot of its own pushed
        # every subtitle after it late, and into the next scene.
        head = ""
        if scene["kind"] in ("title", "outro"):
            head = f"[{scene['title']} – {scene['sub']}]\n"
        elif scene["kind"] == "section":
            head = f"[{scene['n']} {scene['name']}]\n"
        for text, _ in entry["cards"]:
            seconds = reading_time(text)
            out.append(f"{index}\n{stamp(at)} --> {stamp(at + seconds)}\n{head}{text}\n")
            head = ""
            at += seconds
            index += 1
    path.write_text("\n".join(out), encoding="utf-8")
    return index - 1


# --------------------------------------------------------------------- render

def scene_frames(entry, fonts, total_seconds):
    """Every frame of one scene, with its subtitles swapping inside it."""
    scene = entry["scene"]
    frames = int(max(entry["seconds"] * FPS, 1))
    kind = scene["kind"]

    if kind in ("title", "outro"):
        key = "outro" if kind == "outro" else "title"
        bounds, at = [], 0.0
        for text, lines in entry["cards"]:
            seconds = reading_time(text)
            bounds.append((at, at + seconds, lines))
            at += seconds
        for i in range(frames):
            now = i / FPS
            lines = bounds[-1][2] if bounds else []
            for start, end, candidate in bounds:
                if start <= now < end:
                    lines = candidate
                    break
            done = (entry["start"] + now) / total_seconds
            yield title_frame(fonts, scene["title"], scene["sub"], lines, key, done)
        return

    if kind == "section":
        blurb = entry["cards"][0][1] if entry["cards"] else []
        for i in range(frames):
            done = (entry["start"] + i / FPS) / total_seconds
            yield section_frame(fonts, scene["n"], scene["name"], blurb, done)
        return

    # a screen: the push runs across the whole scene, subtitles change within it
    shot = SHOTS / f"{scene['shot']}.png"
    if not shot.exists():
        raise FileNotFoundError(f"{shot} - run media/capture.py first")

    bounds, at = [], 0.0
    for text, lines in entry["cards"]:
        seconds = reading_time(text)
        bounds.append((at, at + seconds, lines))
        at += seconds

    for i in range(frames):
        now = i / FPS
        lines = bounds[-1][2]
        for start, end, candidate in bounds:
            if start <= now < end:
                lines = candidate
                break
        done = (entry["start"] + now) / total_seconds
        yield screen_frame(fonts, shot, scene["label"], lines,
                           i / max(frames - 1, 1), entry["section"], done,
                           scene.get("focus"))


def render(timeline, fonts, total_seconds):
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(WORK / "video.mp4")],
        stdin=subprocess.PIPE)

    written, last = 0, None
    for i, entry in enumerate(timeline):
        for j, frame in enumerate(scene_frames(entry, fonts, total_seconds)):
            shown = frame
            if last is not None and j < XFADE:      # dissolve out of the last scene
                shown = Image.blend(last, frame, (j + 1) / XFADE)
            proc.stdin.write(shown.tobytes())
            written += 1
        last = frame
        scene = entry["scene"]
        print(f"  {i:02d}  {scene['kind']:8} {entry['seconds']:5.1f}s  "
              f"{len(entry['cards'])} card(s)  {scene.get('label', scene.get('name', ''))}")
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("ffmpeg failed while writing the video track.")
    return written


def mux(bed):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(WORK / "video.mp4"), "-i", str(bed),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", "-movflags", "+faststart", str(OUT)], check=True)


def main():
    WORK.mkdir(parents=True, exist_ok=True)

    print("Planning")
    fonts, timeline, total = plan()
    cards = sum(len(e["cards"]) for e in timeline)
    print(f"  {len(SCENES)} scenes, {cards} subtitles, {total / 60:.1f} minutes\n")

    print("Rendering frames")
    written = render(timeline, fonts, total)

    print("\nComposing the bed")
    bed = music.write(written / FPS + 0.5)

    print("Subtitles")
    count = write_srt(timeline)
    print(f"  {SRT.name}, {count} entries")

    print("Muxing")
    mux(bed)

    size = OUT.stat().st_size / 1048576
    print("\n" + "=" * 68)
    print(f"  {OUT}")
    print(f"  {written / FPS / 60:.1f} minutes, {W}x{H}, {size:.0f} MB, no voice track")
    print("=" * 68)


if __name__ == "__main__":
    main()
