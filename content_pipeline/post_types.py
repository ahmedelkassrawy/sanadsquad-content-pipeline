import re
from pathlib import Path

import yaml

from .config import (
    build_prompt,
    check_type,
    check_writer_skills,
    load_angles,
    local_file,
    parse_brand,
    read_agent_file,
    read_type_file,
    read_utf8,
)
from .contracts import Angle, Rubric, TopicInfo, WriteStyle
from .models import (
    AgentConfig,
    BrandRules,
    MarketingFormat,
    PostType,
    ResearchPack,
    ResolvedSlot,
    SourceRecord,
)


def load_rubric(root: Path, spec: PostType, read=read_utf8) -> Rubric:
    """Parse the criteria front matter of a post type's rubric file."""
    text = read(local_file(root / "post_types" / spec.name, spec.rubric))

    if not text.startswith("---\n"):
        raise ValueError("rubric needs criteria front matter")

    return Rubric.model_validate(yaml.safe_load(text.split("---", 2)[1]))


def first_sentence(text: str, limit: int = 200) -> str:
    """The first sentence of a description, cut at `limit` characters."""
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0][:limit]


class PostTypes:
    """Answer what each post type means for one run, reading every file at most once."""

    def __init__(self, root: Path):
        self.root = root
        self._type_files = {}
        self._specs = {}
        self._agents = {}
        self._writers = None
        self._prompts = {}
        self._rubrics = {}
        self._angles = {}
        self._hints = {}
        self._brand = None
        self._texts = {}

    def text(self, path: Path) -> str:
        """Return a run file as UTF-8 text, reading it at most once."""
        if path not in self._texts:
            self._texts[path] = read_utf8(path)

        return self._texts[path]

    def _type_file(self, name: str) -> PostType:
        if name not in self._type_files:
            path = local_file(self.root / "post_types", name + "/type.yaml")

            self._type_files[name] = read_type_file(path)

        return self._type_files[name]

    def _writer_names(self) -> set[str]:
        if self._writers is None:
            folder = self.root / "post_types"

            self._writers = {
                self._type_file(path.parent.name).writer
                for path in sorted(folder.glob("*/type.yaml"))
            }

        return self._writers

    def spec(self, slot_type: str) -> PostType:
        """Return a checked post type: its files exist and its writer cannot search."""
        if slot_type not in self._specs:
            spec = self._type_file(slot_type)

            check_type(self.root, slot_type, spec)

            self.agent(spec.writer, writer=True)

            self._specs[slot_type] = spec

        return self._specs[slot_type]

    def agent(self, name: str, *, writer: bool = False) -> AgentConfig:
        """Return an agent config, refusing a searching agent that some post type uses to write."""
        if name not in self._agents:
            self._agents[name] = read_agent_file(self.root, name)

        config = self._agents[name]

        check_writer_skills(config, writer or name in self._writer_names())

        return config

    def writer(self, slot_type: str) -> str:
        """Return the name of the agent that writes this post type."""
        return self.spec(slot_type).writer

    def prompt(self, agent_name: str, slot_type: str | None = None) -> str:
        """Return an agent's full system prompt, with the post type text when one is given."""
        key = (agent_name, slot_type)

        if key not in self._prompts:
            self._prompts[key] = build_prompt(
                self.root,
                agent_name,
                self.agent(agent_name),
                self.spec(slot_type) if slot_type else None,
                self.text,
            )

        return self._prompts[key]

    def rubric(self, slot_type: str) -> Rubric:
        """Return the scoring criteria the reviewer applies to this post type."""
        if slot_type not in self._rubrics:
            self._rubrics[slot_type] = load_rubric(self.root, self.spec(slot_type), self.text)

        return self._rubrics[slot_type]

    def hints(self, slot_type: str) -> str:
        """Return the text describing what good sources for this post type look like."""
        if slot_type not in self._hints:
            spec = self.spec(slot_type)

            self._hints[slot_type] = self.text(
                local_file(self.root / "post_types" / slot_type, spec.research.hints)
            )

        return self._hints[slot_type]

    def angles(self, slot_type: str):
        """Return the angles of an angles-sourced post type."""
        if slot_type not in self._angles:
            angles = load_angles(self.root, self.spec(slot_type))
            rules, _ = self.brand()
            configured = {stat_id for angle in angles for stat_id in angle.stat_ids or []}

            if configured - set(rules.approved_stats):
                raise ValueError("angle stat_ids reference an unknown approved stat")

            self._angles[slot_type] = angles

        return self._angles[slot_type]

    def brand(self) -> tuple[BrandRules, str]:
        """Return the brand rules and voice prose."""
        if self._brand is None:
            self._brand = parse_brand(self.text(self.root / "inputs/brand-voice.md"))

        return self._brand

    def topic_pool(self, slot_type: str, kb, *, ranked: bool = False) -> list[str]:
        """Return the topic ids a slot of this type may draw from: kb entries or angles."""
        if self.spec(slot_type).research.topic_source == "angles":
            return [angle.id for angle in self.angles(slot_type)]

        if ranked:
            return [e["id"] for e in kb.search(self.hints(slot_type), len(kb.entries))]

        return [entry["id"] for entry in kb.entries]

    def topic_info(self, slot_type: str, kb) -> dict[str, TopicInfo]:
        """Describe every id in `topic_pool`: a kb entry by name, category, severity and summary.

        An angle has only a name in `angles.yaml`, so that is all the topic chooser gets for it.
        """
        if self.spec(slot_type).research.topic_source == "angles":
            return {angle.id: TopicInfo(title=angle.name) for angle in self.angles(slot_type)}

        return {
            entry["id"]: TopicInfo(
                title=entry["name"],
                summary=first_sentence(entry["description"]),
                category=", ".join(entry["category"])
                if isinstance(entry["category"], list)
                else entry["category"],
                severity=entry["severity"],
            )
            for entry in kb.entries
        }

    def choose_angle_source(self, slot: ResolvedSlot, week_topics: set[str]) -> str | None:
        """For a pick-one angle, the first candidate source no other slot this week covers.

        Returns None for any other slot. Falls back to the first candidate when every candidate
        is taken; the caller records that as a duplicate-topic warning.
        """
        if self.spec(slot.type).research.topic_source != "angles" or not slot.topic_id:
            return None

        # A focused slot's topic is a researched KB or web id, not an angle.
        angle = next((a for a in self.angles(slot.type) if a.id == slot.topic_id), None)

        if angle is None or not angle.pick_one:
            return None

        return next((id for id in angle.source_ids if id not in week_topics), angle.source_ids[0])

    def writing_style(self, slot: ResolvedSlot, plan: list[ResolvedSlot]) -> WriteStyle | None:
        """The hook and closing style for a slot: its index among the plan's slots of its type.

        The first slot of a type gets the first style, the second the second, wrapping around, so
        the lanes need no shared state to write differently. None when the type has no styles.
        """
        spec = self.spec(slot.type)
        same_type = [other.slot_id for other in plan if other.type == slot.type]
        index = same_type.index(slot.slot_id) if slot.slot_id in same_type else 0

        def pick(styles: list[str]) -> str | None:
            return styles[index % len(styles)] if styles else None

        hook = pick(spec.hook_styles)
        closing = pick(spec.closing_styles)

        return WriteStyle(hook=hook, closing=closing) if hook or closing else None

    def format(self, slot: ResolvedSlot) -> MarketingFormat | None:
        """Return the selected marketing format, refusing stale or invented ids."""
        if slot.type != "marketing-post" or slot.format is None:
            return None

        if not self.spec(slot.type).formats:
            # A snapshot saved before marketing formats used the type's original one-unit rule.
            return None

        selected = next(
            (format for format in self.spec(slot.type).formats if format.id == slot.format), None
        )

        if selected is None:
            raise ValueError("unknown marketing format: " + slot.format)

        return selected

    def marketing_angle(self, slot: ResolvedSlot) -> Angle | None:
        """The angle a marketing slot names by topic id, or by an angle id or name in focus."""
        if slot.type != "marketing-post":
            return None

        angles = self.angles(slot.type)
        configured = next((angle for angle in angles if angle.id == slot.topic_id), None)
        if configured:
            return configured

        focus = (slot.focus or "").strip().casefold()
        if not focus:
            return None

        return next(
            (angle for angle in angles if focus in {angle.id.casefold(), angle.name.casefold()}),
            None,
        )

    def marketing_service_line(self, slot: ResolvedSlot) -> str | None:
        """Resolve an angle or focused marketing request to one configured service line."""
        if slot.type != "marketing-post":
            return None

        configured = self.marketing_angle(slot)
        if configured and configured.service_line:
            return configured.service_line
        if configured and configured.service_lines:
            return configured.service_lines[0]

        focus = (slot.focus or "").casefold()
        matches = {
            angle.service_line
            for angle in self.angles(slot.type)
            if angle.service_line and any(term.casefold() in focus for term in angle.focus_terms)
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def marketing_service_lines(self, slot: ResolvedSlot) -> list[str] | None:
        """The two service lines of a cross-service angle, primary first; None otherwise."""
        if slot.type != "marketing-post":
            return None
        configured = self.marketing_angle(slot)
        return list(configured.service_lines) if configured and configured.service_lines else None

    def assigned_pack(self, slot: ResolvedSlot, kb) -> ResearchPack | None:
        """Return the pack for an already-assigned topic, or None when research must search."""
        if not slot.topic_id or slot.focus:
            return None

        if self.spec(slot.type).research.topic_source == "kb":
            source = kb.source(slot.topic_id)

            return ResearchPack(
                slot_id=slot.slot_id,
                topic_id=source.source_id,
                topic_name=source.title,
                sources=[source],
            )

        angle = next(a for a in self.angles(slot.type) if a.id == slot.topic_id)
        rules, _ = self.brand()

        sources = [
            SourceRecord(source_id=id, kind="stat", title=rules.brand_name, evidence_text=text)
            for id, text in rules.approved_stats.items()
            if angle.stat_ids is None or id in angle.stat_ids
        ]

        if angle.pick_one:
            chosen = slot.angle_source or angle.source_ids[0]
            sources = [kb.source(chosen)] + sources
        elif angle.source_ids is not None:
            sources = [kb.source(source_id) for source_id in angle.source_ids] + sources

        return ResearchPack(
            slot_id=slot.slot_id,
            topic_id=slot.topic_id,
            topic_name=angle.name,
            sources=sources,
            kb_allowed=angle.kb_allowed or angle.subject == "vulnerability-class",
        )
