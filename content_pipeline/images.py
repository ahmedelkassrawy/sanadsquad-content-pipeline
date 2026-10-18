import re
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFont
from .models import ImageSpec
from skills import brand_rules


CARD_SIZE = (1200, 675)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")


@dataclass(frozen=True)
class CardStyle:
    """What code, not the visual model, adds to a card: all of it comes from the approved post.

    template is the post type's visual template ("technical" draws a code panel, "brand" draws
    the brand layout), label names the topic, and code is the post's own first inline code span.
    """

    template: str = "brand"
    brand_name: str = ""
    label: str = ""
    code: str | None = None


def card_style(request, brand_name: str) -> CardStyle:
    """Build the card style for an approved piece from its post type, pack and text."""
    template = request.post_type.visual.template
    code = None

    if template == "technical":
        for tweet in request.draft.tweets:
            match = _CODE_SPAN.search(tweet.text)

            if match:
                code = match.group(1).strip()

                break

    label = request.research.topic_name if template == "technical" else ""

    return CardStyle(template=template, brand_name=brand_name, label=label, code=code)


def overlay_errors(spec, request, rules):
    errors = brand_rules.check_brand(spec.overlay_text, rules)

    texts = (
        [tweet.text for tweet in request.draft.tweets]
        + [source.evidence_text for source in request.research.sources if source.status == "ok"]
        + [source.title for source in request.research.sources if source.status == "ok"]
    )

    def normalized(text):
        quotes = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
        return " ".join(text.translate(quotes).casefold().split())

    overlay = normalized(spec.overlay_text)

    if not overlay or not any(overlay in normalized(text) for text in texts):
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


_FONTS = {
    "regular": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"],
    "bold": [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    ],
    "mono": ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf", "DejaVuSansMono.ttf"],
}


def font(size, weight="regular"):
    for path in _FONTS[weight]:
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


def _fit(draw, text, width, height, largest, smallest, weight):
    """The largest font size whose wrapped lines fit the box, with those lines."""
    for size in range(largest, smallest - 1, -2):
        typeface = font(size, weight)
        lines = wrap(draw, text, typeface, width)
        ascent, descent = typeface.getmetrics()

        if len(lines) * (ascent + descent + 12) <= height:
            return typeface, lines

    raise ValueError("overlay cannot fit canvas")


def _shorten(draw, text, typeface, width):
    """Cut text with an ellipsis so one line fits the width."""
    if draw.textlength(text, font=typeface) <= width:
        return text

    while text and draw.textlength(text + "...", font=typeface) > width:
        text = text[:-1]

    return text.rstrip() + "..."


def _draw_grove(draw, accent, navy, width, height):
    """A row of outlined geometric trees in the lower right: decoration only, no words."""
    ground = height - 72
    sizes = [130, 190, 150, 220, 170]
    x = width - 140

    for size in reversed(sizes):
        half = size // 4
        top = ground - 24 - size
        draw.polygon(
            [(x - half, ground - 24), (x, top), (x + half, ground - 24)], outline=accent, width=4
        )
        draw.line([(x, ground - 24), (x, ground)], fill=accent, width=4)
        x -= 112

    draw.line([(x + 40, ground), (width - 64, ground)], fill=accent, width=3)


def render_card(
    spec: ImageSpec,
    path: Path,
    background: Image.Image | None = None,
    style: CardStyle | None = None,
):
    """Draw the approved overlay, brand header, code panel and badge over a plain or generated
    background. Every word on the card comes from the approved spec or the approved post."""
    style = style or CardStyle()
    navy, accent, white = (ImageColor.getrgb(color) for color in spec.palette[:3])
    width, height = CARD_SIZE

    if background is not None:
        image = background.convert("RGB").resize(CARD_SIZE)
        # A navy panel that fades to the right keeps the text readable and the picture visible.
        shade = Image.new("L", CARD_SIZE, 0)
        fade = ImageDraw.Draw(shade)

        for x in range(width):
            fade.line([(x, 0), (x, height)], fill=int(235 * max(0.0, min(1.0, 1.4 - x / 760))))

        image = Image.composite(Image.new("RGB", CARD_SIZE, navy), image, shade)
        text_width = 660
    else:
        image = Image.new("RGB", CARD_SIZE, navy)
        text_width = 1000

    draw = ImageDraw.Draw(image)

    # Left accent bar and the brand header.
    draw.rectangle((64, 64, 72, height - 64), fill=accent)

    header = style.brand_name.upper()

    if style.label:
        header = f"{header}  /  {style.label.upper()}" if header else style.label.upper()

    if header:
        header_font = font(22, "bold")
        draw.text(
            (100, 64),
            _shorten(draw, header, header_font, text_width),
            font=header_font,
            fill=accent,
        )

    # Title, sentence-cased from the approved overlay.
    title = spec.overlay_text.strip()
    title = title[:1].upper() + title[1:]
    code_panel = style.template == "technical" and bool(style.code)
    title_height = 250 if code_panel else 380
    typeface, lines = _fit(draw, title, text_width, title_height, 64, 20, "bold")
    ascent, descent = typeface.getmetrics()
    y = 120

    for line in lines:
        draw.text((100, y), line, font=typeface, fill=white)
        y += ascent + descent + 12

    # A short amber rule under the title.
    draw.rectangle((100, y + 8, 196, y + 13), fill=accent)

    if code_panel:
        mono = font(30, "mono")
        code = _shorten(draw, style.code, mono, text_width - 56)
        top = y + 64
        panel = tuple(max(0, channel - 14) for channel in navy)
        draw.rounded_rectangle(
            (100, top, 100 + text_width, top + 96), radius=14, fill=panel, outline=accent, width=2
        )
        draw.text((128, top + 30), code, font=mono, fill=white)

    if style.template != "technical" and background is None:
        _draw_grove(draw, accent, navy, width, height)

    if spec.severity:
        badge_font = font(24, "bold")
        label = spec.severity.upper()
        badge_width = int(draw.textlength(label, font=badge_font)) + 48
        draw.rounded_rectangle(
            (100, height - 120, 100 + badge_width, height - 72), radius=12, fill=accent
        )
        draw.text((124, height - 113), label, font=badge_font, fill=navy)

    path.parent.mkdir(parents=True, exist_ok=True)

    image.save(path, format="PNG")
