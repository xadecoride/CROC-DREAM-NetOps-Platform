from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from netops.enums import Platform
from netops.intent.models import DiffFilters

# Правила Ани: что выбрасывать из конфигов перед сравнением и какие секции сравнивать.
DIFF_FILTERS_PATH = Path(__file__).with_name("diff_filters.yaml")


@dataclass(frozen=True, slots=True)
class NormalizationRules:
    indent_unit: int
    comment_prefixes: tuple[str, ...]
    drop_lines: tuple[re.Pattern[str], ...] = ()
    drop_blocks: tuple[re.Pattern[str], ...] = ()
    mask_secrets: tuple[tuple[re.Pattern[str], str], ...] = ()
    managed_sections: tuple[re.Pattern[str], ...] = ()
    implicit_lines: frozenset[str] = frozenset()

    def is_managed(self, line: str) -> bool:
        return not self.managed_sections or any(p.search(line) for p in self.managed_sections)


class ConfigNormalizer:
    def __init__(self, rules: Mapping[Platform | str, NormalizationRules] | None = None) -> None:
        self._rules = dict(rules) if rules is not None else dict(_default_rules())

    @classmethod
    def from_file(cls, path: Path | None) -> ConfigNormalizer:
        return cls(load_diff_filters(path) if path is not None else None)

    def rules_for(self, platform: Platform | str) -> NormalizationRules:
        if platform in self._rules:
            return self._rules[platform]
        if isinstance(platform, str):
            try:
                enum_plat = Platform(platform)
                if enum_plat in self._rules:
                    return self._rules[enum_plat]
            except ValueError:
                _ = None
        return self._rules[platform]

    def normalize(
        self,
        platform: Platform | str,
        text: str,
        *,
        managed_only: bool = True,
    ) -> str:
        rules = self.rules_for(platform)
        entries = list(_filter_lines(text, rules, managed_only=managed_only))
        if not entries:
            return ""
        return "\n".join(_reindent(entries, rules.indent_unit)) + "\n"


@lru_cache(maxsize=1)
def _default_rules() -> Mapping[Platform | str, NormalizationRules]:
    return load_diff_filters(DIFF_FILTERS_PATH)


def load_diff_filters(path: Path) -> dict[Platform | str, NormalizationRules]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    platforms = raw.get("platforms") if isinstance(raw, dict) else None
    if not isinstance(platforms, dict):
        raise ValueError(f"{path}: expected a 'platforms' mapping")
    parsed = DiffFilters.model_validate(raw)
    platforms = parsed.platforms
    implicit = parsed.common.implicit_lines_to_drop_from_intended

    rules: dict[Platform | str, NormalizationRules] = {}
    for name, spec in platforms.items():
        try:
            plat_key: Platform | str = Platform(name)
        except ValueError:
            plat_key = str(name)
        rules[plat_key] = _platform_rules(spec.model_dump(), implicit.get(str(name)) or ())

    missing = sorted(set(Platform) - set(rules.keys()))
    if missing:
        raise ValueError(f"{path}: no rules for platform(s) {', '.join(missing)}")
    return rules



def _platform_rules(spec: Mapping[str, Any], implicit: Iterable[str]) -> NormalizationRules:
    indent = re.match(r"\d+", str(spec.get("indent", "1")))
    return NormalizationRules(
        indent_unit=int(indent.group()) if indent else 1,
        comment_prefixes=(str(spec.get("comment_prefix", "!")),),
        drop_lines=_compile(item["re"] for item in spec.get("drop_line_patterns") or ()),
        drop_blocks=_compile(item["start_re"] for item in spec.get("drop_blocks") or ()),
        mask_secrets=tuple(
            (re.compile(item["re"]), str(item["replace"]))
            for item in spec.get("mask_secrets") or ()
        ),
        managed_sections=_compile(spec.get("managed_sections") or ()),
        implicit_lines=frozenset(implicit),
    )


def _compile(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in patterns)


# Регулярки применяются к строке с отступом: ^end$ ловит только конец файла, а не вложенный end.
# Отброшенный блок или неуправляемая секция уходят вместе со всеми вложенными строками.
# Неявные строки (no shutdown) убираем с обеих сторон: на устройстве они не видны.
def _filter_lines(
    text: str,
    rules: NormalizationRules,
    *,
    managed_only: bool = True,
) -> Iterator[tuple[int, str]]:
    skip_deeper_than: int | None = None
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(8).split("\n"):
        line = raw_line.rstrip()
        content = line.lstrip()
        if not content:
            continue
        indent = len(line) - len(content)
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        for pattern, replacement in rules.mask_secrets:
            line = pattern.sub(replacement, line)
        content = line.lstrip()
        if content.startswith(rules.comment_prefixes):
            continue
        if any(pattern.search(line) for pattern in rules.drop_lines):
            continue
        if any(pattern.search(line) for pattern in rules.drop_blocks) or (
            managed_only and indent == 0 and not rules.is_managed(line)
        ):
            skip_deeper_than = indent
            continue
        if content in rules.implicit_lines:
            continue
        yield indent, content


# Вложенность определяем по относительным отступам, поэтому шаблон с 4 пробелами
# и вывод Arista с 3 пробелами дают одинаковый текст.
def _reindent(entries: Iterable[tuple[int, str]], unit: int) -> Iterator[str]:
    open_levels: list[int] = []
    for indent, content in entries:
        while open_levels and open_levels[-1] > indent:
            open_levels.pop()
        if not open_levels or open_levels[-1] < indent:
            open_levels.append(indent)
        depth = len(open_levels) - 1
        yield " " * (unit * depth) + content
