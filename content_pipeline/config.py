import json
import os
from pathlib import Path
from typing import Literal
import yaml
from dotenv import dotenv_values
from pydantic import Field
from .models import AgentConfig, BrandRules, Contract, PostType, ResolvedSlot, SourceRegistry
from .contracts import AnglesFile
from skills.search_kb import KnowledgeBase


class Settings(Contract):
    default_model: str = "openrouter:openai/gpt-4.1-mini"
    model_override: str | None = None
    reasoning_effort_override: Literal["none", "low", "medium", "high"] | None = None
    max_tweet_chars: int = Field(default=280, ge=1, le=280)
    run_token_budget: int = Field(default=400000, ge=1)
    run_cost_budget_usd: float = Field(default=1.0, gt=0)
    allow_unpriced: bool = False
    advisor_enabled: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)

    @classmethod
    def load(cls, root: Path, overrides: dict | None = None):
        env = {**dotenv_values(root / ".env"), **os.environ}
        keys = {k.upper(): k for k in cls.model_fields}
        values = {keys[k]: v for k, v in env.items() if k in keys and v not in (None, "")}

        return cls.model_validate({**values, **(overrides or {})})


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()

    if not path.is_relative_to(root.resolve()):
        raise ValueError("configuration path escapes root")

    if not path.is_file():
        raise ValueError(f"missing configuration file: {name}")

    return path


def load_brand(path: Path) -> tuple[BrandRules, str]:
    return parse_brand(path.read_text(encoding="utf-8"))


def load_sources(path: Path) -> SourceRegistry:
    """Load the source allowlist; invalid or incomplete lists fail before research starts."""
    return SourceRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))


def parse_brand(text: str) -> tuple[BrandRules, str]:
    """Split the brand voice file into its rules and prose."""
    if not text.startswith("---\n"):
        raise ValueError("brand voice needs YAML front matter")

    _, header, prose = text.split("---", 2)

    return BrandRules.model_validate(yaml.safe_load(header)), prose.strip()


def read_type_file(path: Path) -> PostType:
    """Parse one type.yaml file without checking the files it names."""
    return PostType.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def check_type(root: Path, name: str, spec: PostType) -> None:
    """Check that a parsed post type matches its folder and names files that exist."""
    path = root / "post_types" / name / "type.yaml"

    if spec.name != name:
        raise ValueError("post type name differs from folder")

    for file in (spec.rubric, "writer.md", spec.research.hints, spec.research.angles):
        if file:
            local_file(path.parent, file)

    local_file(root / "agents", spec.writer + "/system.md")


def load_type(root: Path, name: str) -> PostType:
    path = local_file(root / "post_types", name + "/type.yaml")
    spec = read_type_file(path)

    check_type(root, name, spec)

    load_agent(root, spec.writer, writer=True)

    return spec


def load_angles(root: Path, spec: PostType):
    path = local_file(root / "post_types" / spec.name, spec.research.angles)
    angles = AnglesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).angles
    configured_sources = {source_id for angle in angles for source_id in angle.source_ids or []}

    if configured_sources:
        kb_ids = {
            entry["id"] for entry in KnowledgeBase(local_file(root, "inputs/kb.json")).entries
        }

        if configured_sources - kb_ids:
            raise ValueError("angle evidence references an unknown knowledge-base source")

    return angles


def load_plan(root: Path, path: Path) -> list[ResolvedSlot]:
    slots = [
        ResolvedSlot.model_validate(x)
        for x in json.loads(path.read_text(encoding="utf-8"))["slots"]
    ]

    if not slots or len({s.slot_id for s in slots}) != len(slots):
        raise ValueError("plan needs unique slots")

    for slot in slots:
        load_type(root, slot.type)

    return slots


def read_agent_file(root: Path, name: str) -> AgentConfig:
    """Parse one agent.yaml file without checking whether the agent is a writer."""
    return AgentConfig.model_validate(
        yaml.safe_load(
            local_file(root / "agents", name + "/agent.yaml").read_text(encoding="utf-8")
        )
    )


def check_writer_skills(config: AgentConfig, writer: bool) -> None:
    """Refuse a writer agent that is allowed to search."""
    if writer and any(s in {"search_kb", "web_search"} for s in config.skills):
        raise ValueError("writers cannot search")


def load_agent(root: Path, name: str, *, writer: bool = False) -> AgentConfig:
    config = read_agent_file(root, name)

    if not writer:
        folder = root / "post_types"

        for path in sorted(folder.glob("*/type.yaml")):
            spec = read_type_file(path)

            if spec.writer == name:
                writer = True

                break

    check_writer_skills(config, writer)

    return config


def resolve_model(settings: Settings, agent: AgentConfig) -> str:
    return settings.model_override or agent.model or settings.default_model


def load_prompt(root: Path, agent_name: str, post_type: str | None = None) -> str:
    agent = load_agent(root, agent_name)
    spec = load_type(root, post_type) if post_type else None

    return build_prompt(root, agent_name, agent, spec)


def read_utf8(path: Path) -> str:
    """Read a text file as UTF-8."""
    return path.read_text(encoding="utf-8")


def build_prompt(
    root: Path, agent_name: str, agent: AgentConfig, spec: PostType | None, read=read_utf8
) -> str:
    """Join the agent system prompt, post type text, skill texts and brand voice."""
    parts = [read(local_file(root / "agents", agent_name + "/system.md"))]

    if spec:
        parts.append(
            read(
                local_file(
                    root / "post_types" / spec.name,
                    "writer.md" if agent_name == spec.writer else spec.rubric,
                )
            )
        )

    for skill in agent.skills:
        parts.append(read(local_file(root / "skills", skill + ".md")))

    parts.append(read(local_file(root / "inputs", "brand-voice.md")))

    return "\n\n".join(parts)


def load_visual_backends(root: Path) -> list[str]:
    """Read the ordered image backend chain from the run configuration."""
    data = yaml.safe_load(local_file(root, "config/visual.yaml").read_text(encoding="utf-8"))
    backends = data.get("backends") if isinstance(data, dict) else None

    if (
        not isinstance(backends, list)
        or not backends
        or any(backend not in ("cloudflare_flux", "pillow_card") for backend in backends)
    ):
        raise ValueError("invalid visual backends")

    return backends
