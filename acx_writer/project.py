"""Book folder layout, saved step results, and prompt templates."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_DIR / "prompts"
OUTPUT_DIR = REPO_DIR / "output"


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", text).strip()


def render(template: str, **values) -> str:
    """Fill {{name}} placeholders in prompts/<template>.md."""
    text = (PROMPTS_DIR / f"{template}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    missing = re.findall(r"\{\{(\w+)\}\}", text)
    if missing:
        raise ValueError(f"Prompt {template} has unfilled placeholders: {missing}")
    return text


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


@dataclass
class Book:
    title: str
    subtitle: str
    pen_name: str
    word_count: int = 15000
    english_style: str = "American"
    root: Path = field(init=False)

    def __post_init__(self):
        self.root = OUTPUT_DIR / safe_name(self.pen_name) / safe_name(self.title)
        self.work.mkdir(parents=True, exist_ok=True)
        self.research.mkdir(parents=True, exist_ok=True)

    @property
    def work(self) -> Path:
        """Intermediate results, kept so a failed run can resume (not uploaded)."""
        return self.root / ".work"

    @property
    def research(self) -> Path:
        return self.root / "Book Research"

    @property
    def file_title(self) -> str:
        return safe_name(self.title)

    @property
    def num_chapters(self) -> int:
        # ~1,000-word introduction and conclusion, ~2,500 words per body chapter.
        return max(1, round((self.word_count - 2000) / 2500))

    @property
    def copyright(self) -> str:
        year = datetime.date.today().year
        return (
            f"Copyright © {year} {self.pen_name}. All rights reserved.\n\n"
            "No part of this publication may be reproduced, distributed, or transmitted in any form "
            "or by any means without the prior written permission of the author, except for brief "
            "quotations in reviews and certain other non-commercial uses permitted by copyright law.\n\n"
            "Disclaimer: This book is for informational and educational purposes only. It is not a "
            "substitute for professional medical, psychological, legal, or financial advice. The stories "
            "in this book are illustrative composites; names and details are fictional. If you are "
            "struggling, please reach out to a qualified professional."
        )

    # ── saved step results ────────────────────────────────────────────────
    def load(self, name: str):
        path = self.work / name
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if name.endswith(".json") else text

    def save(self, name: str, value):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            path.write_text(value, encoding="utf-8")
        return value

    def step(self, name: str, make):
        """Return the saved result for `name`, or run `make()` and save it."""
        saved = self.load(name)
        if saved is not None:
            print(f"  ↷ {name} already done, reusing it")
            return saved
        return self.save(name, make())
