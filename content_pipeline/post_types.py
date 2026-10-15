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
from .contracts import Rubric
from .models import AgentConfig, BrandRules, PostType, ResearchPack, ResolvedSlot, SourceRecord


def load_rubric(root: Path, spec: PostType, read=read_utf8) -> Rubric:
    """Parse the criteria front matter of a post type's rubric file."""
    text = read(local_file(root / "post_types" / spec.name, spec.rubric))

    if not text.startswith("---\n"):
        raise ValueError("rubric needs criteria front matter")

    return Rubric.model_validate(yaml.safe_load(text.split("---", 2)[1]))


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

    def _read(self, path: Path) -> str:
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
                self._read,
            )

        return self._prompts[key]

    def rubric(self, slot_type: str) -> Rubric:
        """Return the scoring criteria the reviewer applies to this post type."""
        if slot_type not in self._rubrics:
            self._rubrics[slot_type] = load_rubric(self.root, self.spec(slot_type), self._read)

        return self._rubrics[slot_type]

    def hints(self, slot_type: str) -> str:
        """Return the text describing what good sources for this post type look like."""
        if slot_type not in self._hints:
            spec = self.spec(slot_type)

            self._hints[slot_type] = self._read(
                local_file(self.root / "post_types" / slot_type, spec.research.hints)
            )

        return self._hints[slot_type]

    def angles(self, slot_type: str):
        """Return the angles of an angles-sourced post type."""
        if slot_type not in self._angles:
            self._angles[slot_type] = load_angles(self.root, self.spec(slot_type))

        return self._angles[slot_type]

    def brand(self) -> tuple[BrandRules, str]:
        """Return the brand rules and voice prose."""
        if self._brand is None:
            self._brand = parse_brand(self._read(self.root / "inputs/brand-voice.md"))

        return self._brand

    def topic_pool(self, slot_type: str, kb, *, ranked: bool = False) -> list[str]:
        """Return the topic ids a slot of this type may draw from: kb entries or angles."""
        if self.spec(slot_type).research.topic_source == "angles":
            return [angle.id for angle in self.angles(slot_type)]

        if ranked:
            return [e["id"] for e in kb.search(self.hints(slot_type), len(kb.entries))]

        return [entry["id"] for entry in kb.entries]

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
        ]

        if angle.source_ids is not None:
            sources = [kb.source(source_id) for source_id in angle.source_ids] + sources

        return ResearchPack(
            slot_id=slot.slot_id, topic_id=slot.topic_id, topic_name=angle.name, sources=sources
        )
