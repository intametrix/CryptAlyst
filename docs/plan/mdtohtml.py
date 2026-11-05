#!/usr/bin/env python3
"""A small markdown reader for the master plan: enough of the language, none of the guessing.

Handles headings, paragraphs, lists, tables, fenced code, blockquotes, horizontal rules and
the plan's own <p class="figtitle">/<p class="figcap"> figure convention. Figures are looked
up by the order they appear in the source, which is the order they were rendered in.
"""
import re

def inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # restore the handful of tags the source legitimately carries
    for tag in ("b", "/b", "i", "/i", "code", "/code", "br/", "br"):
        s = s.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)
    return s


def convert(md, figdir, fig_path):
    lines = md.split("\n")
    i, out = 0, []
    pending_title = pending_cap = None
    fig_no = 0
    list_open = None

    def close_list():
        nonlocal list_open
        if list_open:
            out.append(f"</{list_open}>")
            list_open = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # figure title / caption carried as raw HTML by the source
        m = re.match(r'<p class="figtitle">(.*?)</p>', stripped)
        if m:
            pending_title = m.group(1); i += 1; continue
        m = re.match(r'<p class="figcap">(.*?)</p>', stripped)
        if m:
            pending_cap = m.group(1); i += 1; continue

        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            body = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i]); i += 1
            i += 1
            close_list()
            if lang == "mermaid":
                fig_no += 1
                cap = pending_cap or pending_title or ""
                out.append(fig_path(fig_no, cap))
            else:
                out.append("<pre>" + "\n".join(body).replace("&", "&amp;")
                           .replace("<", "&lt;").replace(">", "&gt;") + "</pre>")
            pending_title = pending_cap = None
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            close_list()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            width = len(header)
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(h)}</th>" for h in header) + "</tr></thead><tbody>")
            for r in rows:
                r = (r + [""] * width)[:width]
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        if re.match(r"^#{1,4} ", stripped):
            close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            out.append(f"<h{min(level + 1, 4)}>{inline(text)}</h{min(level + 1, 4)}>")
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            close_list(); out.append("<hr>"); i += 1; continue

        if stripped.startswith(">"):
            close_list()
            body = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip()); i += 1
            out.append("<blockquote>" + inline(" ".join(body)) + "</blockquote>")
            continue

        m = re.match(r"^(\s*)([-*])\s+(.*)$", line)
        if m:
            if list_open != "ul":
                close_list(); out.append("<ul>"); list_open = "ul"
            out.append(f"<li>{inline(m.group(3))}</li>"); i += 1; continue

        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            if list_open != "ol":
                close_list(); out.append("<ol>"); list_open = "ol"
            out.append(f"<li>{inline(m.group(3))}</li>"); i += 1; continue

        if stripped == "":
            close_list(); i += 1; continue

        close_list()
        para = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if nxt == "" or nxt.startswith(("#", "|", "```", ">", "-", "*")) or re.match(r"^\d+\.", nxt):
                break
            para.append(nxt); i += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")

    close_list()
    return "\n".join(out)
