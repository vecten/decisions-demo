"""Mine Bash tool calls from Claude Code session logs into data/commands.jsonl.

Claude Code writes one JSONL per session under ~/.claude/projects/<encoded-cwd>/.
Assistant turns carry message.content[] items of type "tool_use"; for Bash the
input has "command" and usually "description". We keep both: the description is
what makes the "misleading" question meaningful.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)

SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*\S+'),
    re.compile(r'sk-[A-Za-z0-9_-]{16,}'),
    re.compile(r'ghp_[A-Za-z0-9]{20,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'),
]

# Rough risk buckets for stratified sampling. Not labels, just a way to make sure
# the sample isn't 95 `ls` and `pytest`.
RISKY = re.compile(r'\brm\b|--force|-f\b|curl[^|]*\|\s*(ba)?sh|sudo|chmod|chown|git (push|reset --hard|clean)|DROP |truncate|> ?/|kill|docker (rm|system prune)')
WRITES = re.compile(r'\bmv\b|\bcp\b|>|tee|sed -i|git (commit|checkout|rebase|stash)|npm (install|i)\b|pip install|uv (add|sync)')


def scrub(text: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1) if m.lastindex else None
        return f'{name}=<redacted>' if name else '<redacted>'

    for pat in SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def bucket(cmd: str) -> str:
    if RISKY.search(cmd):
        return 'risky'
    if WRITES.search(cmd):
        return 'writes'
    return 'reads'


def iter_tool_calls(root: Path):
    for path in root.glob('*/*.jsonl'):
        project = path.parent.name
        with path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get('type') != 'assistant':
                    continue
                content = (rec.get('message') or {}).get('content') or []
                if not isinstance(content, list):
                    continue
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('name') == 'Bash':
                        inp = item.get('input') or {}
                        cmd = inp.get('command')
                        if cmd:
                            yield {
                                'project': project,
                                'session': path.stem,
                                'command': scrub(cmd.strip()),
                                'description': scrub((inp.get('description') or '').strip()),
                            }


@app.command()
def main(
    root: Path = typer.Option(Path.home() / '.claude' / 'projects', help='Claude Code projects dir'),
    out: Path = typer.Option(Path('data/commands.jsonl')),
    n: int = typer.Option(100),
    seed: int = typer.Option(7),
    mix: str = typer.Option('40,30,30', help='risky,writes,reads share in percent'),
):
    """Collect, dedupe, scrub and stratify Bash tool calls."""
    seen: set[str] = set()
    buckets: dict[str, list[dict]] = {'risky': [], 'writes': [], 'reads': []}
    for call in iter_tool_calls(root):
        h = hashlib.sha1(call['command'].encode()).hexdigest()
        if h in seen or len(call['command']) > 600:
            continue
        seen.add(h)
        call['id'] = h[:10]
        call['bucket'] = bucket(call['command'])
        buckets[call['bucket']].append(call)

    rng = random.Random(seed)
    shares = [int(x) for x in mix.split(',')]
    sample: list[dict] = []
    for (name, items), share in zip(buckets.items(), shares):
        k = min(len(items), round(n * share / 100))
        sample += rng.sample(items, k)
    rng.shuffle(sample)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w') as f:
        for rec in sample:
            f.write(json.dumps(rec) + '\n')
    typer.echo(f'{len(seen)} unique commands found; wrote {len(sample)} to {out} '
               f'({ {k: len(v) for k, v in buckets.items()} } available per bucket)')
    typer.echo('Now read the file. Anything you would not put on a screen: delete the line.')


if __name__ == '__main__':
    app()
