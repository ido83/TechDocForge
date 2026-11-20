"""Local Mermaid rendering using Python, with optional local Mermaid CLI support."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
# The optional renderer is invoked directly with fixed arguments.
import subprocess  # nosec B404
import tempfile
import textwrap
from pathlib import Path
import sys

MERMAID_FENCE = re.compile(r"(?msi)^```mermaid[^\S\r\n]*\r?\n(.*?)^```[^\S\r\n]*(?=\r?$)")
_EMPTY_DIAGRAM_SECTION = re.compile(
    r"(?mi)^##[ \t]+Diagrams[ \t]*#*[ \t]*(?:\r?\n|$)"
    r"(?=\s*(?:^#{1,6}[ \t]|\Z))"
)
_DIAGRAM_DECLARATION = re.compile(r"(?:flowchart|graph)(?:\s+(?:TB|TD|BT|LR|RL))?|sequenceDiagram", re.I)


def find_mermaid_cli() -> list[str] | None:
    """Return a command prefix for Mermaid CLI, or None if it is unavailable."""
    # Windows can route batch launchers through a shell even with shell=False.
    executable = "mmdc.exe" if sys.platform == "win32" else "mmdc"
    for directory in map(Path, os.get_exec_path()):
        if not directory.is_absolute():
            continue
        mmdc = shutil.which(str(directory / executable))
        if mmdc and Path(mmdc).name.lower() == executable:
            return [mmdc]
    return None


def _node_label(text: str) -> str:
    text = text.strip()
    for opening, closing in (("[", "]"), ("(", ")"), ("{", "}"), ("<", ">")):
        if opening in text:
            text = text.split(opening, 1)[1].rsplit(closing, 1)[0]
            break
    return text.strip() or "Node"


def _render_mermaid_python(code: str, output: Path) -> Path | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if not lines:
        return None

    if lines[0].lower().startswith(("flowchart", "graph")):
        direction = "LR" if "LR" in lines[0].upper() or "RL" in lines[0].upper() else "TB"
        reverse = "RL" in lines[0].upper()
        nodes: dict[str, str] = {}
        edges: list[tuple[str, str, str]] = []
        edge_re = re.compile(r"^(.+?)\s*(-->|-.->|==>|---|->)\s*(.+?)$")

        def add_node(spec: str) -> str:
            match = re.match(r"^([A-Za-z0-9_-]+)(.*)$", spec.strip())
            if not match:
                node_id = spec.strip()
                label = _node_label(node_id)
            else:
                node_id, suffix = match.groups()
                label = _node_label(node_id + suffix)
            nodes.setdefault(node_id, label)
            return node_id

        for line in lines[1:]:
            if line.lower().startswith(("subgraph", "end", "style ", "classDef ")):
                continue
            match = edge_re.match(line)
            if match:
                left, right = match.group(1).strip(), match.group(3).strip()
                label = ""
                label_match = re.match(r"^\|(.+?)\|\s*(.+)$", right)
                if label_match:
                    label, right = label_match.group(1).strip(), label_match.group(2).strip()
                left_id, right_id = add_node(left), add_node(right)
                edges.append((left_id, right_id, label))
            else:
                match = re.match(r"^([A-Za-z0-9_-]+)(.*)$", line)
                if match:
                    add_node(line)

        if not nodes:
            return None
        try:
            font = ImageFont.load_default(size=18)
        except TypeError:
            font = ImageFont.load_default()

        labels: dict[str, list[str]] = {}
        widths: dict[str, int] = {}
        heights: dict[str, int] = {}
        line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 4
        for key, label in nodes.items():
            wrapped: list[str] = []
            current = ""
            for word in label.split():
                candidate = f"{current} {word}".strip()
                if current and font.getlength(candidate) > 250:
                    wrapped.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                wrapped.append(current)
            if not wrapped:
                wrapped = [key]
            labels[key] = wrapped
            widths[key] = max(150, min(290, int(max(font.getlength(part) for part in wrapped)) + 36))
            heights[key] = max(68, line_height * len(wrapped) + 28)

        outgoing = {key: [] for key in nodes}
        indegree = {key: 0 for key in nodes}
        for left, right, _label in edges:
            outgoing[left].append(right)
            indegree[right] += 1
        ranks = {key: 0 for key in nodes}
        queue = [key for key in nodes if indegree[key] == 0]
        processed: set[str] = set()
        while queue:
            node = queue.pop(0)
            processed.add(node)
            for target in outgoing[node]:
                ranks[target] = max(ranks[target], ranks[node] + 1)
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        for key in nodes:
            if key not in processed:
                ranks[key] = max(ranks.values(), default=0) + list(nodes).index(key)

        layers: dict[int, list[str]] = {}
        for key in nodes:
            layers.setdefault(ranks[key], []).append(key)
        if reverse:
            for layer in layers.values():
                layer.reverse()

        margin, node_gap, layer_gap = 48, 48, 100
        layer_sizes: dict[int, tuple[int, int, int, int]] = {}
        for rank, layer in layers.items():
            layer_sizes[rank] = (
                max(widths[key] for key in layer),
                sum(heights[key] for key in layer) + node_gap * (len(layer) - 1),
                sum(widths[key] for key in layer) + node_gap * (len(layer) - 1),
                max(heights[key] for key in layer),
            )
        positions: dict[str, tuple[int, int]] = {}
        max_vertical_span = max(size[1] for size in layer_sizes.values())
        max_horizontal_span = max(size[2] for size in layer_sizes.values())
        offset = margin
        for rank in sorted(layers):
            layer = layers[rank]
            layer_width, layer_height, layer_stack_width, layer_max_height = layer_sizes[rank]
            if direction == "LR":
                y = margin + (max_vertical_span - layer_height) // 2
                for key in layer:
                    positions[key] = (offset + (layer_width - widths[key]) // 2, y)
                    y += heights[key] + node_gap
                offset += layer_width + layer_gap
            else:
                x = margin + (max_horizontal_span - layer_stack_width) // 2
                for key in layer:
                    positions[key] = (x, offset + (layer_max_height - heights[key]) // 2)
                    x += widths[key] + node_gap
                offset += layer_max_height + layer_gap

        canvas_w = max(640, max(x + widths[key] for key, (x, _y) in positions.items()) + margin)
        canvas_h = max(360, max(y + heights[key] for key, (_x, y) in positions.items()) + margin)
        image = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        outgoing_edges: dict[str, list[int]] = {key: [] for key in nodes}
        incoming_edges: dict[str, list[int]] = {key: [] for key in nodes}
        for edge_index, (left, right, _label) in enumerate(edges):
            outgoing_edges[left].append(edge_index)
            incoming_edges[right].append(edge_index)

        for edge_index, (left, right, label) in enumerate(edges):
            x1, y1 = positions[left]
            x2, y2 = positions[right]
            out_idx = outgoing_edges[left].index(edge_index)
            in_idx = incoming_edges[right].index(edge_index)
            source_fraction = (out_idx + 1) / (len(outgoing_edges[left]) + 1)
            target_fraction = (in_idx + 1) / (len(incoming_edges[right]) + 1)
            cx1, cy1 = x1 + widths[left] / 2, y1 + heights[left] / 2
            cx2, cy2 = x2 + widths[right] / 2, y2 + heights[right] / 2
            if direction == "LR":
                start = (x1 + widths[left], y1 + heights[left] * source_fraction) if cx2 >= cx1 \
                    else (x1, y1 + heights[left] * source_fraction)
                end = (x2, y2 + heights[right] * target_fraction) if cx2 >= cx1 \
                    else (x2 + widths[right], y2 + heights[right] * target_fraction)
            else:
                start = (x1 + widths[left] * source_fraction, y1 + heights[left]) if cy2 >= cy1 \
                    else (x1 + widths[left] * source_fraction, y1)
                end = (x2 + widths[right] * target_fraction, y2) if cy2 >= cy1 \
                    else (x2 + widths[right] * target_fraction, y2 + heights[right])
            draw.line((start, end), fill=(55, 65, 81, 255), width=3)
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = max(1, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            base_x, base_y = end[0] - ux * 13, end[1] - uy * 13
            perp_x, perp_y = -uy * 7, ux * 7
            draw.polygon([end, (base_x + perp_x, base_y + perp_y),
                          (base_x - perp_x, base_y - perp_y)], fill=(55, 65, 81, 255))
            if label:
                lx, ly = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
                bounds = draw.textbbox((0, 0), label, font=font)
                label_box = (lx - 3, ly - 2, lx + bounds[2] - bounds[0] + 6,
                             ly + bounds[3] - bounds[1] + 4)
                draw.rectangle(label_box, fill=(255, 255, 255, 230))
                draw.text((lx, ly), label, fill=(75, 85, 99, 255), font=font)

        for key, label in nodes.items():
            x, y = positions[key]
            w, h = widths[key], heights[key]
            draw.rounded_rectangle((x, y, x + w, y + h), radius=12,
                                   fill=(219, 234, 254, 255), outline=(37, 99, 235, 255), width=3)
            text_y = y + (h - line_height * len(labels[key])) // 2
            for text_line in labels[key]:
                box = draw.textbbox((0, 0), text_line, font=font)
                draw.text((x + (w - (box[2] - box[0])) / 2, text_y),
                          text_line, fill=(30, 64, 175, 255), font=font)
                text_y += line_height

        image.save(output, "PNG")
        return output

    if lines[0].lower() == "sequencediagram":
        participants: list[str] = []
        messages: list[tuple[str, str, str]] = []
        arrow_re = re.compile(r"^(.+?)(->>|-->>|->|-x)(.+?):\s*(.*)$")
        for line in lines[1:]:
            participant = re.match(r"^participant\s+(.+)$", line, re.IGNORECASE)
            actor = re.match(r"^actor\s+(.+)$", line, re.IGNORECASE)
            if participant or actor:
                name = (participant or actor).group(1).strip()
                if name not in participants:
                    participants.append(name)
            else:
                match = arrow_re.match(line)
                if match:
                    left, _arrow, right, message = match.groups()
                    left, right = left.strip(), right.strip()
                    for name in (left, right):
                        if name not in participants:
                            participants.append(name)
                    messages.append((left, right, message.strip()))
        if not participants:
            return None

        try:
            font = ImageFont.load_default(size=18)
        except TypeError:
            font = ImageFont.load_default()
        participant_widths = {
            name: max(140, int(font.getlength(name)) + 32)
            for name in participants
        }
        gap = 90
        xs: dict[str, int] = {}
        cursor = 60
        for name in participants:
            xs[name] = cursor + participant_widths[name] // 2
            cursor += participant_widths[name] + gap
        canvas_w = max(640, cursor - gap + 60)

        message_rows: list[tuple[int, list[str]]] = []
        y = 120
        for left, right, message in messages:
            max_text_width = max(120, abs(xs[right] - xs[left]) - 48)
            words = message.split()
            wrapped: list[str] = []
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and font.getlength(candidate) > max_text_width:
                    wrapped.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                wrapped.append(current)
            row_height = max(72, len(wrapped) * 22 + 54)
            message_rows.append((y, wrapped))
            y += row_height
        canvas_h = max(280, y + 50)
        image = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        for name, x in xs.items():
            box = draw.textbbox((0, 0), name, font=font)
            w = participant_widths[name]
            draw.rounded_rectangle((x - w / 2, 30, x + w / 2, 74), 8,
                                   fill=(219, 234, 254, 255), outline=(37, 99, 235, 255), width=2)
            draw.text((x - (box[2] - box[0]) / 2, 42), name, fill=(30, 64, 175, 255), font=font)
            draw.line((x, 74, x, canvas_h - 30), fill=(156, 163, 175, 255), width=2)
        for (left, right, _message), (row_y, wrapped) in zip(messages, message_rows):
            x1, x2 = xs[left], xs[right]
            arrow_y = row_y + len(wrapped) * 22 + 20
            draw.line((x1, arrow_y, x2, arrow_y), fill=(55, 65, 81, 255), width=3)
            arrow_direction = 1 if x2 >= x1 else -1
            draw.polygon([(x2, arrow_y), (x2 - 10 * arrow_direction, arrow_y - 7),
                          (x2 - 10 * arrow_direction, arrow_y + 7)], fill=(55, 65, 81, 255))
            if wrapped:
                text_width = max(font.getlength(part) for part in wrapped)
                text_x = (x1 + x2 - text_width) / 2
                text_y = row_y
                for part in wrapped:
                    box = draw.textbbox((0, 0), part, font=font)
                    draw.rectangle((text_x - 3, text_y - 1, text_x + box[2] - box[0] + 3,
                                    text_y + box[3] - box[1] + 2), fill=(255, 255, 255, 230))
                    draw.text((text_x, text_y), part, fill=(55, 65, 81, 255), font=font)
                    text_y += 20
        image.save(output, "PNG")
        return output

    return None


def render_mermaid(code: str, output: str | Path) -> Path | None:
    """Render Mermaid source to PNG locally, using Python for common diagrams."""
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    python_rendered = _render_mermaid_python(code, out)
    if python_rendered:
        return python_rendered

    command = find_mermaid_cli()
    if not command:
        return None
    with tempfile.TemporaryDirectory(prefix="techdocforge-mermaid-") as td:
        directory = Path(td)
        source = directory / "diagram.mmd"
        image = directory / "diagram.png"
        source.write_text(code.strip() + "\n", encoding="utf-8")
        try:
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # The model supplies only file contents. Filenames and flags are fixed.
            result = subprocess.run(  # nosec B603
                command + ["-i", source.name, "-o", image.name, "-b", "transparent"],
                cwd=directory,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
                **kwargs,
            )
            if result.returncode != 0 or not image.is_file() or image.stat().st_size == 0:
                return None
            shutil.copyfile(image, out)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return out


def split_mermaid_diagrams(markdown: str) -> tuple[str, list[str]]:
    """Extract nonempty Mermaid sources and remove orphaned diagram headings."""
    diagrams = []
    for match in MERMAID_FENCE.finditer(markdown):
        code = match.group(1).strip()
        content = [line.strip() for line in code.splitlines() if line.strip() and not line.lstrip().startswith("%%")]
        if content and not (len(content) == 1 and _DIAGRAM_DECLARATION.fullmatch(content[0])):
            diagrams.append(code)
    body = MERMAID_FENCE.sub("", markdown).strip()
    body = _EMPTY_DIAGRAM_SECTION.sub("", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip(), diagrams


def _append_diagrams(body: str, blocks: list[str]) -> str:
    if blocks:
        body = "\n\n".join(filter(None, (body, "## Diagrams", *blocks)))
    return body + "\n"


def move_mermaid_diagrams_to_bottom(markdown: str) -> str:
    """Append nonempty Mermaid sources in a single Diagrams section."""
    body, diagrams = split_mermaid_diagrams(markdown)
    return _append_diagrams(body, [f"```mermaid\n{code}\n```" for code in diagrams])


def materialize_mermaid_images(
    markdown: str,
    asset_dir: str | Path,
    asset_url_prefix: str = "assets",
    show_source: bool = False,
) -> str:
    """Replace Mermaid fences with generated PNG image links for Markdown export."""
    if show_source:
        return move_mermaid_diagrams_to_bottom(markdown)

    body, diagrams = split_mermaid_diagrams(markdown)
    if not diagrams:
        return body + "\n"
    assets = Path(asset_dir)
    assets.mkdir(parents=True, exist_ok=True)

    images = []
    prefix = asset_url_prefix.rstrip("/")
    for code in diagrams:
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        output = assets / f"diagram-{digest}.png"
        if not output.is_file():
            output.unlink(missing_ok=True)
            render_mermaid(code, output)
        if output.is_file():
            images.append(f"![Mermaid diagram]({prefix}/{output.name})")
    return _append_diagrams(body, images)
