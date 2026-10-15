import json
import os
from pathlib import Path
from typing import Literal
import yaml
from dotenv import dotenv_values
from pydantic import Field
from .models import AgentConfig, BrandRules, Contract, PostType, ResolvedSlot
from .contracts import AnglesFile


class Settings(Contract):
    default_model: str = "openrouter:openai/gpt-4.1-mini"
    model_override: str | None = None
    reasoning_effort_override: Literal["none", "low", "medium", "high"] | None = None
    max_tweet_chars: int = Field(default=280, ge=1, le=280)
    run_token_budget: int = Field(default=250000, ge=1)
    run_cost_budget_usd: float = Field(default=1.0, gt=0)
    allow_unpriced: bool = False
    advisor_enabled: bool = True
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
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("brand voice needs YAML front matter")
    _, header, prose = text.split("---", 2)
    return BrandRules.model_validate(yaml.safe_load(header)), prose.strip()


def load_type(root: Path, name: str) -> PostType:
    folder = root / "post_types"
    path = local_file(folder, name + "/type.yaml")
    spec = PostType.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if spec.name != name:
        raise ValueError("post type name differs from folder")
    for file in (spec.rubric, "writer.md", spec.research.hints, spec.research.angles):
        if file:
            local_file(path.parent, file)
    local_file(root / "agents", spec.writer + "/system.md")
    load_agent(root, spec.writer, writer=True)
    return spec


def load_angles(root: Path, spec: PostType):
    path = local_file(root / "post_types" / spec.name, spec.research.angles)
    angles = AnglesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).angles
    configured_sources = {source_id for angle in angles for source_id in angle.source_ids or []}
    if configured_sources:
        kb_ids = {
            entry["id"]
            for entry in json.loads(local_file(root, "inputs/kb.json").read_text(encoding="utf-8"))[
                "entries"
            ]
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


def load_agent(root: Path, name: str, *, writer: bool = False) -> AgentConfig:
    config = AgentConfig.model_validate(
        yaml.safe_load(
            local_file(root / "agents", name + "/agent.yaml").read_text(encoding="utf-8")
        )
    )
    if not writer:
        folder = root / "post_types"
        for path in sorted(folder.glob("*/type.yaml")):
            spec = PostType.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            if spec.writer == name:
                writer = True
                break
    if writer and any(s in {"search_kb", "web_search"} for s in config.skills):
        raise ValueError("writers cannot search")
    return config


def resolve_model(settings: Settings, agent: AgentConfig) -> str:
    return settings.model_override or agent.model or settings.default_model


def load_prompt(root: Path, agent_name: str, post_type: str | None = None) -> str:
    agent = load_agent(root, agent_name)
    parts = [local_file(root / "agents", agent_name + "/system.md").read_text(encoding="utf-8")]
    if post_type:
        spec = load_type(root, post_type)
        parts.append(
            local_file(
                root / "post_types" / post_type,
                "writer.md" if agent_name == spec.writer else spec.rubric,
            ).read_text(encoding="utf-8")
        )
    for skill in agent.skills:
        parts.append(local_file(root / "skills", skill + ".md").read_text(encoding="utf-8"))
    parts.append(local_file(root / "inputs", "brand-voice.md").read_text(encoding="utf-8"))
    return "\n\n".join(parts)
