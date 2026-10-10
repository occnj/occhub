"""Sermon-note import (PDF/DOCX parsing), rich-text rendering and the Oasis Next Steps helpers."""
from core import *

PDF_MAX_PAGES = 50

_DOCX_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
_DOCX_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_BULLET_MARKER_RE = re.compile(r'^[\-\*•●▪‣⁃◦]\s+')
_NUM_MARKER_RE = re.compile(r'^(?:\d{1,3}|[ivxlcdmIVXLCDM]{1,7}|[a-zA-Z])[.)]\s+')
# "C. S. Lewis" / "A. W. Tozer" start exactly like an "A." list marker; never treat those
# as lists, or the initial gets eaten and the sentence turns into a bogus list item.
_INITIALS_RE = re.compile(r'^[A-Za-z]\.\s*[A-Za-z]\.')

def list_marker_kind(text):
    """('ul'|'ol', marker_char_len) when text opens with a list marker, else (None, 0)."""
    if not text or _INITIALS_RE.match(text):
        return None, 0
    match = _BULLET_MARKER_RE.match(text)
    if match:
        return 'ul', match.end()
    match = _NUM_MARKER_RE.match(text)
    if match:
        return 'ol', match.end()
    return None, 0

def sermon_title_from_filename(path):
    base = os.path.splitext(os.path.basename(path or ''))[0]
    base = re.sub(r'[_-]+', ' ', base).strip()
    return base.title() if base else 'Sermon Notes'

# ---------------------------------------------------------------- DOCX

def _docx_toggle_on(rPr, name):
    """True when a run toggle (w:b / w:i) is present and not explicitly switched off.
    Word and Google Docs both emit <w:b w:val="0"/> to *cancel* inherited bold."""
    if rPr is None:
        return False
    el = rPr.find('w:' + name, _DOCX_NS)
    if el is None:
        return False
    val = (el.get(_DOCX_W + 'val') or '').strip().lower()
    return val not in ('0', 'false', 'off')

def _docx_numbering_kinds(docx_zip):
    """Map numId -> 'ul'/'ol' by reading numbering.xml's level-0 numFmt. Best-effort."""
    try:
        xml_data = docx_zip.read('word/numbering.xml')
    except KeyError:
        return {}
    try:
        root = ET.fromstring(xml_data)
        abstract_fmt = {}
        for abstract_num in root.findall('w:abstractNum', _DOCX_NS):
            abs_id = abstract_num.get(_DOCX_W + 'abstractNumId')
            for lvl in abstract_num.findall('w:lvl', _DOCX_NS):
                if lvl.get(_DOCX_W + 'ilvl') == '0':
                    num_fmt_el = lvl.find('w:numFmt', _DOCX_NS)
                    if num_fmt_el is not None:
                        abstract_fmt[abs_id] = num_fmt_el.get(_DOCX_W + 'val')
                    break
        num_kinds = {}
        for num in root.findall('w:num', _DOCX_NS):
            num_id = num.get(_DOCX_W + 'numId')
            abstract_id_el = num.find('w:abstractNumId', _DOCX_NS)
            if abstract_id_el is not None:
                fmt = abstract_fmt.get(abstract_id_el.get(_DOCX_W + 'val'), 'bullet')
                num_kinds[num_id] = 'ul' if fmt in ('bullet', 'none') else 'ol'
        return num_kinds
    except Exception:
        return {}

def _docx_para_runs(para):
    """[text, bold, italic] in document order. Walks the paragraph tree rather than only
    its direct w:r children, so text nested in w:hyperlink (scripture links), w:ins
    (tracked changes) or w:smartTag is not lost, and w:br is honoured wherever it sits.
    Deleted text (w:delText) is skipped because only w:t is read."""
    runs = []

    def walk(node, bold, italic):
        for child in node:
            tag = child.tag.split('}')[-1]
            if tag in ('pPr', 'rPr'):
                continue
            if tag == 'p':
                continue  # nested text-box paragraph; emitted as its own record
            if tag == 'r':
                rPr = child.find('w:rPr', _DOCX_NS)
                walk(child, _docx_toggle_on(rPr, 'b'), _docx_toggle_on(rPr, 'i'))
            elif tag == 't':
                if child.text:
                    runs.append([child.text, bold, italic])
            elif tag == 'br':
                runs.append(['\n', bold, italic])
            elif tag == 'tab':
                runs.append(['\t', bold, italic])
            else:
                walk(child, bold, italic)

    walk(para, False, False)

    merged = []
    for text, bold, italic in runs:
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            merged[-1][0] += text
        else:
            merged.append([text, bold, italic])
    return merged

def docx_records(abs_path):
    """Parse a DOCX once into paragraph records, so the plain-text view (title/summary)
    and the HTML view (body) are always derived from the same list of paragraphs."""
    try:
        with zipfile.ZipFile(abs_path) as docx:
            xml_data = docx.read('word/document.xml')
            num_kinds = _docx_numbering_kinds(docx)
        root = ET.fromstring(xml_data)
        records = []
        for para in root.findall('.//w:p', _DOCX_NS):
            pPr = para.find('w:pPr', _DOCX_NS)
            style_val = ''
            list_kind = None
            if pPr is not None:
                pStyle = pPr.find('w:pStyle', _DOCX_NS)
                if pStyle is not None:
                    style_val = (pStyle.get(_DOCX_W + 'val') or '').lower()
                numPr = pPr.find('w:numPr', _DOCX_NS)
                if numPr is not None:
                    numId_el = numPr.find('w:numId', _DOCX_NS)
                    num_id = numId_el.get(_DOCX_W + 'val') if numId_el is not None else None
                    list_kind = num_kinds.get(num_id, 'ul')
            if not list_kind and style_val.startswith('listbullet'):
                list_kind = 'ul'
            elif not list_kind and style_val.startswith('listnumber'):
                list_kind = 'ol'

            runs = _docx_para_runs(para)
            if not runs:
                continue

            if not list_kind:
                first_text = runs[0][0]
                stripped = first_text.lstrip('\n\t ')
                lead_len = len(first_text) - len(stripped)
                kind, marker_len = list_marker_kind(stripped)
                if kind:
                    list_kind = kind
                    runs[0][0] = first_text[:lead_len] + stripped[marker_len:]

            text = re.sub(r'[\n\t]+', ' ', ''.join(r[0] for r in runs)).strip()
            if not text:
                continue
            records.append({
                'runs': runs,
                'text': text,
                'list_kind': list_kind,
                'heading': style_val.startswith('heading') or style_val in ('title', 'subtitle'),
            })
        return records
    except Exception as e:
        app.logger.warning(f"docx_records() error: {e}")
        return []

def extract_docx_paragraphs(abs_path):
    return [rec['text'] for rec in docx_records(abs_path)]

def _docx_runs_to_html(runs, suppress_bold=False):
    parts = []
    for raw_text, is_bold, is_italic in runs:
        if not raw_text:
            continue
        escaped = html.escape(raw_text).replace('\n', '<br>').replace('\t', '&emsp;')
        if suppress_bold:
            is_bold = False
        if is_bold and is_italic:
            escaped = f'<strong><em>{escaped}</em></strong>'
        elif is_bold:
            escaped = f'<strong>{escaped}</strong>'
        elif is_italic:
            escaped = f'<em>{escaped}</em>'
        parts.append(escaped)
    return ''.join(parts)

def docx_records_to_html(records):
    blocks = []
    list_kind = None
    list_items = []

    def flush_list():
        nonlocal list_kind, list_items
        if list_items:
            items_html = ''.join(f'<li>{item}</li>' for item in list_items)
            blocks.append(f'<{list_kind}>{items_html}</{list_kind}>')
        list_kind = None
        list_items = []

    for rec in records:
        para_html = _docx_runs_to_html(rec['runs'], suppress_bold=rec['heading']).strip()
        if not para_html:
            continue
        if rec['list_kind']:
            if list_kind and list_kind != rec['list_kind']:
                flush_list()
            list_kind = rec['list_kind']
            list_items.append(para_html)
            continue
        flush_list()
        tag = 'h2' if rec['heading'] else 'p'
        blocks.append(f'<{tag}>{para_html}</{tag}>')

    flush_list()
    return '\n'.join(blocks)

def extract_docx_html(abs_path, skip_first=False):
    """Rich HTML from a DOCX: bold, italic, headings, manual line breaks, and lists."""
    records = docx_records(abs_path)
    return docx_records_to_html(records[1:] if skip_first else records)

# ---------------------------------------------------------------- PDF

def _pdf_cluster_words_into_lines(words, tol=2.0):
    lines = []
    current = []
    current_top = None
    for w in sorted(words, key=lambda w: (round(w['top'], 1), w['x0'])):
        top = w['top']
        if current_top is None or abs(top - current_top) <= tol:
            current.append(w)
            if current_top is None:
                current_top = top
        else:
            lines.append(current)
            current = [w]
            current_top = top
    if current:
        lines.append(current)
    return lines

def _pdf_line_size(words):
    sizes = Counter(round(w.get('size') or 0) for w in words)
    return sizes.most_common(1)[0][0] if sizes else 0

def _pdf_runs_html(words, suppress_bold=False):
    if not words:
        return ''
    runs = []
    current_words = []
    current_style = None
    for w in words:
        fname = (w.get('fontname') or '').lower()
        style = (('bold' in fname) and not suppress_bold, 'italic' in fname or 'oblique' in fname)
        if current_style is None or style == current_style:
            current_words.append(w['text'])
            current_style = style
        else:
            runs.append((current_style, current_words))
            current_words = [w['text']]
            current_style = style
    if current_words:
        runs.append((current_style, current_words))
    parts = []
    for (is_bold, is_italic), run_words in runs:
        text = html.escape(' '.join(run_words))
        if is_bold and is_italic:
            text = f'<strong><em>{text}</em></strong>'
        elif is_bold:
            text = f'<strong>{text}</strong>'
        elif is_italic:
            text = f'<em>{text}</em>'
        parts.append(text)
    return ' '.join(parts)

def pdf_read_lines(abs_path, max_pages=PDF_MAX_PAGES):
    """Positioned text lines from a PDF. Single source of truth for title and body."""
    lines = []
    try:
        with pdfplumber.open(abs_path) as pdf:
            pages = pdf.pages
            if len(pages) > max_pages:
                app.logger.warning(
                    f"pdf_read_lines: {abs_path} has {len(pages)} pages, parsing first {max_pages}")
                pages = pages[:max_pages]
            for page_index, page in enumerate(pages):
                words = page.extract_words(extra_attrs=['fontname', 'size'], use_text_flow=False)
                if not words:
                    continue
                for i, line_words in enumerate(_pdf_cluster_words_into_lines(words)):
                    lines.append({
                        'words': line_words,
                        'top': line_words[0]['top'],
                        'x0': min(w['x0'] for w in line_words),
                        'x1': max(w['x1'] for w in line_words),
                        'page_width': float(page.width or 0),
                        'text': ' '.join(w['text'] for w in line_words),
                        'new_page': i == 0 and page_index > 0,
                    })
    except Exception as e:
        app.logger.warning(f"pdf_read_lines() error: {e}")
        return []
    return lines

def _pdf_layout_metrics(lines):
    """Body font size, modal line leading, and the left/right edges of the text block."""
    size_counter = Counter()
    for line in lines:
        for w in line['words']:
            size_counter[round(w.get('size') or 0)] += 1
    body_size = size_counter.most_common(1)[0][0] if size_counter else 12
    body_lines = [l for l in lines if _pdf_line_size(l['words']) == body_size]

    gap_counter = Counter()
    prev = None
    for line in lines:
        if (prev is not None and not line['new_page']
                and _pdf_line_size(prev['words']) == body_size
                and _pdf_line_size(line['words']) == body_size):
            gap = round(line['top'] - prev['top'])
            if gap > 0:
                gap_counter[gap] += 1
        prev = line
    leading = gap_counter.most_common(1)[0][0] if gap_counter else max(body_size * 1.6, 1)

    left_edge = min((l['x0'] for l in body_lines), default=0)
    # Assume symmetric margins to locate the true right margin. Using the widest observed
    # line instead would be circular: in notes where no line reaches the margin (an outline
    # of short points) the longest point *defines* the edge, so every line looks "full" and
    # the whole document collapses into one paragraph.
    page_width = max((l.get('page_width') or 0) for l in lines) if lines else 0
    observed_right = max((l['x1'] for l in body_lines), default=0)
    right_edge = max(observed_right, page_width - left_edge) if page_width else observed_right
    return body_size, leading, left_edge, right_edge

def pdf_lines_to_html(lines):
    """Rich HTML from positioned PDF lines: bold runs, size-based headings, lists, paragraphs."""
    if not lines:
        return ''
    body_size, leading, left_edge, right_edge = _pdf_layout_metrics(lines)
    # A wrapped line runs out to the right margin; the last line of a paragraph stops short.
    # Spacing alone cannot separate one-line paragraphs, which all share the same gap.
    # Ragged-right wrapping leaves up to a long word unused, so allow ~15% of the measure:
    # across sample notes, wrapped lines fell short by <=53pt and real paragraph ends by
    # >=151pt, so the threshold sits in open space between the two populations.
    measure = max(right_edge - left_edge, 1)
    fill_tol = max(measure * 0.15, body_size * 2.5)

    blocks = []
    list_kind = None
    list_items = []
    para_words = []
    prev_line = None

    def flush_list():
        nonlocal list_kind, list_items
        if list_items:
            items_html = ''.join(f'<li>{item}</li>' for item in list_items)
            blocks.append(f'<{list_kind}>{items_html}</{list_kind}>')
        list_kind = None
        list_items = []

    def flush_para():
        nonlocal para_words
        if para_words:
            blocks.append(f'<p>{_pdf_runs_html(para_words)}</p>')
        para_words = []

    for line in lines:
        words = line['words']
        size = _pdf_line_size(words)

        if size >= body_size * 1.15 and len(line['text']) <= 90:
            flush_list()
            flush_para()
            tag = 'h2' if size >= body_size * 1.4 else 'h3'
            blocks.append(f'<{tag}>{_pdf_runs_html(words, suppress_bold=True)}</{tag}>')
            prev_line = line
            continue

        kind, marker_len = list_marker_kind(line['text'])
        if kind:
            flush_para()
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            item_words = words[1:] if len(words[0]['text']) < marker_len else words
            list_items.append(_pdf_runs_html(item_words))
            prev_line = line
            continue

        flush_list()
        continues_para = (
            bool(para_words) and prev_line is not None and not line['new_page']
            and (line['top'] - prev_line['top']) <= leading * 1.35
            and prev_line['x1'] >= right_edge - fill_tol
            and line['x0'] <= left_edge + body_size
        )
        if not continues_para:
            flush_para()
        para_words.extend(words)
        prev_line = line

    flush_list()
    flush_para()
    return '\n'.join(blocks)

def extract_pdf_html(abs_path, skip_first=False):
    lines = pdf_read_lines(abs_path)
    return pdf_lines_to_html(lines[1:] if skip_first else lines)

RICH_TEXT_TAGS = ['p', 'br', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'ul', 'ol', 'li', 'blockquote']
_RICH_TEXT_TAG_RE = re.compile(r'<(' + '|'.join(RICH_TEXT_TAGS) + r')[ >/]', re.I)

def sanitize_rich_html(raw):
    return bleach.clean(raw or '', tags=RICH_TEXT_TAGS, attributes={}, strip=True)

@app.template_filter('richtext')
def richtext_filter(raw):
    """Render admin-entered story/notes text as HTML: sanitizes it if it already
    contains our rich-text tags (from the admin formatting toolbar), otherwise
    treats it as legacy plain text and auto-wraps blank-line-separated paragraphs."""
    raw = raw or ''
    if not _RICH_TEXT_TAG_RE.search(raw):
        escaped = html.escape(raw)
        paragraphs = [p.strip() for p in escaped.split('\n\n') if p.strip()] or ([escaped.strip()] if escaped.strip() else [])
        return Markup(''.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs))
    return Markup(sanitize_rich_html(raw))

@app.template_filter('nl2br')
def nl2br_filter(raw):
    """Plain admin text -> HTML with line breaks. Escapes first, so it is safe to print."""
    return Markup(html.escape(raw or '').replace('\n', '<br>'))

def build_sermon_note_payload(path_rel, ext, fallback_title='', summary=''):
    abs_path = os.path.join(BASE_DIR, 'static', path_rel)
    title = fallback_title.strip() if fallback_title else ''
    # Both branches parse the document once and derive the title, the summary and the
    # body from that same parse, so the title can never be taken from one extractor
    # while the body comes from another.
    if ext == 'docx':
        units = docx_records(abs_path)
        plain_lines = [rec['text'] for rec in units]
        to_html = docx_records_to_html
    else:
        units = pdf_read_lines(abs_path)
        plain_lines = [line['text'] for line in units]
        to_html = pdf_lines_to_html

    if not title and plain_lines:
        title = plain_lines[0][:120].strip()
        units = units[1:]
        plain_lines = plain_lines[1:]
    if not title:
        title = sermon_title_from_filename(path_rel)
    if not summary:
        source_line = next((l for l in plain_lines if len(l.split()) > 6), '')
        summary = source_line[:180].strip() if source_line else 'Sermon notes for this message.'
    body_html = to_html(units)
    if not body_html and plain_lines:
        body_html = '\n'.join(f'<p>{html.escape(l)}</p>' for l in plain_lines)
    return {
        'title': title,
        'summary': summary,
        'body_html': body_html or '<p>Sermon notes were uploaded, but the document did not contain readable text.</p>',
        'source_file': path_rel,
    }

def latest_sermon_note(conn):
    return conn.execute(
        "SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC LIMIT 1"
    ).fetchone()

NEXT_STEPS_LABEL = 'Oasis Next Steps'

def row_value(row, key, default=''):
    """Read a column off a sqlite3.Row without blowing up on rows that predate
    a migration (sqlite3.Row raises IndexError for unknown keys)."""
    if row is None:
        return default
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value

MAX_NEXT_STEPS_FILES = 10
_UPLOAD_STEM_RE = re.compile(r'_[0-9a-f]{8}$')

def next_steps_label(note):
    """Heading for the whole take-home section on a note."""
    return (row_value(note, 'next_steps_title') or '').strip() or NEXT_STEPS_LABEL

def note_next_steps(conn, nid):
    return conn.execute(
        "SELECT * FROM sermon_next_steps WHERE note_id=? ORDER BY sort_order, id", (nid,)
    ).fetchall()

def next_steps_counts(conn):
    """note_id -> number of attached documents, for the archive and admin lists."""
    return {
        r['note_id']: r['n'] for r in conn.execute(
            "SELECT note_id, COUNT(*) AS n FROM sermon_next_steps GROUP BY note_id"
        ).fetchall()
    }

def next_steps_item_label(item):
    """Name shown in the download list. Falls back to the filename with the uuid
    that save_document_file() appends stripped back off, so a cleared name box
    reads "Family Devotional" rather than "Family Devotional 75E0Dc29"."""
    explicit = (row_value(item, 'title') or '').strip()
    if explicit:
        return explicit
    base = os.path.splitext(os.path.basename(row_value(item, 'file')))[0]
    return sermon_title_from_filename(_UPLOAD_STEM_RE.sub('', base))

def next_steps_download_name(note, item):
    """Filename the phone saves it under — uploads keep a uuid stem on disk, which
    is useless to someone hunting for the file later in their Files app."""
    ext = os.path.splitext(row_value(item, 'file'))[1].lower() or '.pdf'
    stem = re.sub(r'[^A-Za-z0-9]+', '-', f"{next_steps_item_label(item)} {row_value(note, 'note_date')}").strip('-')
    return (stem or 'oasis-next-steps') + ext

def next_steps_file_meta(item):
    """'PDF · 240 KB' for the download list, or just the type if the file is gone."""
    stored = row_value(item, 'file')
    kind = (os.path.splitext(stored)[1].lstrip('.') or 'file').upper()
    try:
        size = os.path.getsize(os.path.join(UPLOAD_FOLDER, os.path.basename(stored)))
    except OSError:
        return kind
    if size >= 1024 * 1024:
        return f"{kind} · {size / (1024 * 1024):.1f} MB"
    return f"{kind} · {max(1, round(size / 1024))} KB"

app.jinja_env.globals['max_next_steps'] = MAX_NEXT_STEPS_FILES
app.jinja_env.filters['next_steps_label'] = next_steps_label
app.jinja_env.filters['next_steps_item_label'] = next_steps_item_label
app.jinja_env.filters['next_steps_file_meta'] = next_steps_file_meta



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
