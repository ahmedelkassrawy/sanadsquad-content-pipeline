import re
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFont
from .models import ImageSpec
from skills import brand_rules


def overlay_errors(spec, request, rules):
    errors = brand_rules.check_brand(spec.overlay_text, rules)

    texts = (
        [tweet.text for tweet in request.draft.tweets]
        + [source.evidence_text for source in request.research.sources if source.status == "ok"]
        + [source.title for source in request.research.sources if source.status == "ok"]
    )

    if not spec.overlay_text.strip() or not any(spec.overlay_text in text for text in texts):
        errors.append("ungrounded_overlay")

    supported_numbers = set(
        re.findall(
            r"(?<!\w)\d+(?:[.,]\d+)*",
            " ".join(
                [tweet.text for tweet in request.draft.tweets]
                + [
                    source.evidence_text
                    for source in request.research.sources
                    if source.status == "ok"
                ]
            ),
        )
    )

    if any(
        number not in supported_numbers
        for number in re.findall(r"(?<!\w)\d+(?:[.,]\d+)*", spec.overlay_text)
    ):
        errors.append("new_overlay_number")

    if spec.palette != rules.palette:
        errors.append("brand_palette")

    expected = request.severity if request.post_type.visual.severity_badge == "auto" else None

    if spec.severity not in {None, expected}:
        errors.append("source_severity")

    if spec.severity:
        errors.extend(brand_rules.check_brand(spec.severity, rules))

    return errors


def fallback_spec(request, rules):
    texts = (
        [request.research.topic_name]
        + [source.title for source in request.research.sources if source.status == "ok"]
        + [tweet.text for tweet in request.draft.tweets]
    )

    for title in texts:
        spec = ImageSpec(
            concept=title,
            scene=title,
            palette=rules.palette,
            overlay_text=title,
            alt_text=title,
            severity=request.severity
            if request.post_type.visual.severity_badge == "auto"
            else None,
        )

        if spec.severity and brand_rules.check_brand(spec.severity, rules):
            spec.severity = None

        if not overlay_errors(spec, request, rules):
            return spec

    raise ValueError("no grounded brand-safe title")


def font(size):
    for path in ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass

    return ImageFont.load_default(size=size)


def wrap(draw, text, typeface, width):
    lines = []
    line = ""

    for word in text.split():
        # Split an unusually long token without drawing outside the card.
        chunks = []
        chunk = ""

        for character in word:
            if chunk and draw.textlength(chunk + character, font=typeface) > width:
                chunks.append(chunk)

                chunk = ""

            chunk += character

        chunks.append(chunk)

        for chunk in chunks:
            candidate = (line + " " + chunk).strip()

            if line and draw.textlength(candidate, font=typeface) > width:
                lines.append(line)

                line = chunk
            else:
                line = candidate

    if line:
        lines.append(line)

    return lines


def render_card(spec: ImageSpec, path: Path):
    image = Image.new("RGB", (1200, 675), ImageColor.getrgb(spec.palette[0]))
    draw = ImageDraw.Draw(image)
    selected = None

    for size in range(54, 11, -2):
        typeface = font(size)
        lines = wrap(draw, spec.overlay_text, typeface, 1040)

        ascent, descent = typeface.getmetrics()

        height = len(lines) * (ascent + descent + 16)

        if height <= 430:
            selected = (typeface, lines)

            break

    if selected is None:
        raise ValueError("overlay cannot fit canvas")

    typeface, lines = selected
    y = 110

    for line in lines:
        draw.text((80, y), line, font=typeface, fill=spec.palette[-1])

        ascent, descent = typeface.getmetrics()

        y += ascent + descent + 16

    if spec.severity:
        draw.rounded_rectangle((80, 550, 320, 615), radius=12, fill=spec.palette[1])
        draw.text((100, 565), spec.severity.upper(), font=font(28), fill=spec.palette[0])

    path.parent.mkdir(parents=True, exist_ok=True)

    image.save(path, format="PNG")
