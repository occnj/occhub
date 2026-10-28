"""Volunteer Newsletter (/newsletter) and its admin page (/admin/newsletter).

Each issue is two PDFs: a one-page COVER (e.g. "The Nose of a Camel") that the newsletter page
shows, and the FULL newsletter (e.g. "The Table Leaf") that opens when the cover is tapped.
Pages are rendered to JPEGs once, at upload, so phones see the PDF exactly as it was designed
(fonts, colours, layout) without a PDF plug-in; links inside the PDF stay tappable on top of
the picture. The original PDF is kept for Download.
"""
from core import *
from documents import (_pdf_cluster_words_into_lines, _pdf_normalize_line, pdf_lines_to_html,
                       sanitize_rich_html, strip_cid_glyphs)
import pypdfium2 as pdfium

NEWSLETTER_MAX_PAGES = 40
NEWSLETTER_PAGE_WIDTH = 1600           # px; sharp on phones, ~250 KB a page
_LINK_SCHEMES = ('http://', 'https://', 'mailto:', 'tel:')


def _pdf_links(pdf_path):
    """Per page, the PDF's own web links as {url, x, y, w, h} in percent of the page."""
    out = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                links = []
                for h in page.hyperlinks:
                    url = (h.get('uri') or '').strip()
                    if not url.lower().startswith(_LINK_SCHEMES) or not page.width or not page.height:
                        continue
                    links.append({'url': url,
                                  'x': round(100 * h['x0'] / page.width, 2),
                                  'y': round(100 * h['top'] / page.height, 2),
                                  'w': round(100 * (h['x1'] - h['x0']) / page.width, 2),
                                  'h': round(100 * (h['bottom'] - h['top']) / page.height, 2)})
                out.append(links)
    except Exception as e:
        app.logger.warning(f"newsletter: could not read links from {pdf_path}: {e}")
    return out


def _slanted(ch):
    m = ch.get('matrix') or (1, 0, 0, 1)
    return abs(m[1]) > 0.01 or abs(m[2]) > 0.01


def _slanted_runs(chars):
    """Canva sets taglines on a slant; pdfplumber then reads each letter as its own word on its
    own line. Rebuild each slanted run (same slant, in drawing order) as one word."""
    runs, key = [], None
    for ch in chars:
        k = tuple(round(v, 3) for v in ch['matrix'][:4])
        if runs and k == key:
            runs[-1].append(ch)
        else:
            runs.append([ch])
            key = k
    words = []
    for run in runs:
        text = re.sub(r'\s+', ' ', ''.join(c['text'] for c in run)).strip()
        if text:
            words.append({'text': text, 'x0': min(c['x0'] for c in run), 'x1': max(c['x1'] for c in run),
                          'top': min(c['top'] for c in run), 'bottom': max(c['bottom'] for c in run),
                          'fontname': run[0].get('fontname', ''), 'size': run[0].get('size', 0)})
    return words


def _join_spaced_letters(words):
    """Letter-spaced headings ("T H E   N O S E") come back one letter per word. Join the
    letters, keeping a space only where the gap is clearly wider than between letters."""
    if len(words) < 4 or sum(len(w['text']) == 1 for w in words) < 0.8 * len(words):
        return words
    gaps = sorted(b['x0'] - a['x1'] for a, b in zip(words, words[1:]))
    letter_gap = gaps[len(gaps) // 4]
    merged = [dict(words[0])]
    for prev, w in zip(words, words[1:]):
        if w['x0'] - prev['x1'] > letter_gap * 1.6 + 1:
            merged.append(dict(w))
        else:
            merged[-1]['text'] += w['text']
            merged[-1]['x1'] = w['x1']
    return merged


_ORDINALS = {'st', 'nd', 'rd', 'th'}


def _attach_ordinals(words):
    """A small "th" in "19th" sits a few points off its line; put it back next to its number."""
    for w in words:
        if w['text'] in _ORDINALS:
            for n in words:
                if n is not w and n['text'][-1:].isdigit() and abs(n['x1'] - w['x0']) < 2 \
                        and abs(n['top'] - w['top']) < (n.get('size') or 10) * 0.6:
                    w['top'] = n['top']
                    break
    return words


def pdf_text_html(pdf_path):
    """The newsletter as plain, readable text (headings, paragraphs, bold/italic), or '' when
    the PDF has no text (e.g. a scan or a picture)."""
    lines = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages[:NEWSLETTER_MAX_PAGES]):
                chars = [c for c in page.chars if c.get('upright', True)]
                flat = page.filter(lambda o: o.get('object_type') != 'char' or (o.get('upright', True) and not _slanted(o)))
                words = flat.extract_words(extra_attrs=['fontname', 'size'], use_text_flow=False)
                for w in words:
                    w['text'] = strip_cid_glyphs(w['text']).strip()
                words = _attach_ordinals([w for w in words if w['text']]) + _slanted_runs([c for c in chars if _slanted(c)])
                first_on_page = True
                for cluster in _pdf_cluster_words_into_lines(words):
                    line_words = _join_spaced_letters(_pdf_normalize_line(cluster))
                    if not line_words:
                        continue
                    lines.append({'words': line_words, 'top': cluster[0]['top'],
                                  'x0': min(w['x0'] for w in line_words), 'x1': max(w['x1'] for w in line_words),
                                  'page_width': float(page.width or 0),
                                  'text': ' '.join(w['text'] for w in line_words),
                                  'new_page': first_on_page and page_index > 0})
                    first_on_page = False
    except Exception as e:
        app.logger.warning(f"newsletter: could not read text from {pdf_path}: {e}")
        return ''
    if sum(len(l['text']) for l in lines) < 40:
        return ''
    return sanitize_rich_html(pdf_lines_to_html(lines))


def render_pdf_pages(pdf_path):
    """Render every page of a PDF to uploads/<stem>_p<N>.jpg. Returns (pages, error)."""
    try:
        pdf = pdfium.PdfDocument(pdf_path)
    except Exception as e:
        app.logger.warning(f"newsletter: unreadable PDF {pdf_path}: {e}")
        return [], 'That file could not be opened as a PDF.'
    pages = []
    try:
        if len(pdf) == 0:
            return [], 'That PDF has no pages.'
        if len(pdf) > NEWSLETTER_MAX_PAGES:
            return [], f'That PDF has {len(pdf)} pages; the newsletter allows up to {NEWSLETTER_MAX_PAGES}.'
        links = _pdf_links(pdf_path)
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        for i in range(len(pdf)):
            page = pdf[i]
            width = page.get_size()[0] or 612
            img = page.render(scale=NEWSLETTER_PAGE_WIDTH / width).to_pil().convert('RGB')
            name = f'{stem}_p{i + 1}.jpg'
            img.save(os.path.join(UPLOAD_FOLDER, name), 'JPEG', quality=82, optimize=True, progressive=True)
            pages.append({'src': 'uploads/' + name, 'w': img.width, 'h': img.height,
                          'links': links[i] if i < len(links) else []})
        return pages, None
    except Exception as e:
        app.logger.error(f"newsletter: rendering {pdf_path} failed: {e}")
        remove_newsletter_files('', pages)
        return [], 'That PDF could not be turned into pages. Try exporting it again.'
    finally:
        pdf.close()


def save_newsletter_pdf(f):
    """Save an uploaded PDF and render its pages. Returns (pdf_path, pages, error); all empty
    when no file was chosen."""
    if not f or not f.filename:
        return '', [], None
    stem, ext = os.path.splitext(secure_filename(f.filename))
    if ext.lower() != '.pdf' or f.stream.read(5) != b'%PDF-':
        return '', [], 'Please upload a PDF file.'
    f.stream.seek(0)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    name = secure_filename(f'{stem or "newsletter"}_{uuid.uuid4().hex[:8]}.pdf')
    dest = os.path.join(UPLOAD_FOLDER, name)
    f.save(dest)
    pages, error = render_pdf_pages(dest)
    if error:
        remove_newsletter_files('uploads/' + name, [])
        return '', [], error
    return 'uploads/' + name, pages, None


def _still_used(rel):
    """Issues share the cover (it is uploaded once and reused every month), so a file is only
    deleted when no issue refers to it any more."""
    conn = get_db()
    used = conn.execute("SELECT 1 FROM newsletters WHERE cover_pdf=? OR full_pdf=? OR cover_pages LIKE ? "
                        "OR full_pages LIKE ? LIMIT 1", (rel, rel, f'%"{rel}"%', f'%"{rel}"%')).fetchone()
    conn.close()
    return bool(used)


def remove_newsletter_files(pdf, pages):
    for rel in [pdf] + [p.get('src', '') for p in pages]:
        if rel and rel.startswith('uploads/') and '/' not in rel[len('uploads/'):] and not _still_used(rel):
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, rel[len('uploads/'):]))
            except OSError:
                pass


def newsletter_pages(row, part):
    try:
        return json.loads(row[f'{part}_pages'] or '[]')
    except (TypeError, ValueError):
        return []


def _abs_upload(rel):
    return os.path.join(UPLOAD_FOLDER, rel[len('uploads/'):]) if rel and rel.startswith('uploads/') else ''


def newsletter_text(issue, part):
    """The part's text version. Read from the PDF at upload; issues uploaded before text mode
    existed (column still NULL) are read once, the first time someone asks."""
    text = issue[f'{part}_text']
    if text is None:
        path = _abs_upload(issue[f'{part}_pdf'])
        text = pdf_text_html(path) if path and os.path.exists(path) else ''
        conn = get_db()
        conn.execute(f"UPDATE newsletters SET {part}_text=? WHERE id=?", (text, issue['id']))
        conn.commit()
        conn.close()
    return text


def current_cover(conn):
    """The cover new issues reuse: the one on the most recent issue."""
    return conn.execute("SELECT * FROM newsletters WHERE cover_pdf<>'' ORDER BY issue_date DESC, id DESC LIMIT 1").fetchone()


def published_newsletters(conn):
    return conn.execute("SELECT * FROM newsletters WHERE is_published=1 AND cover_pdf<>'' "
                        "ORDER BY issue_date DESC, id DESC").fetchall()


# ---- public pages ---------------------------------------------------------------------------

def _render_issue(issue, part, issues):
    pages = newsletter_pages(issue, part)
    text = newsletter_text(issue, part)
    view = 'text' if request.args.get('view') == 'text' and text else 'original'
    links = [l for p in pages for l in p.get('links', [])]
    return render_template('newsletter.html', settings=all_settings(), issue=issue, part=part, pages=pages,
                           text=text, view=view, links=links,
                           has_full=bool(issue['full_pdf']), issues=issues, pages_of=newsletter_pages)


@app.route('/newsletter')
def newsletter():
    track('newsletter')
    conn = get_db()
    issues = published_newsletters(conn)
    conn.close()
    if not issues:
        return render_template('newsletter.html', settings=all_settings(), issue=None, part='cover', pages=[],
                               text='', view='original', links=[], has_full=False, issues=[])
    return _render_issue(issues[0], 'cover', issues)


def _published_issue(nid):
    conn = get_db()
    issue = conn.execute("SELECT * FROM newsletters WHERE id=? AND is_published=1 AND cover_pdf<>''",
                         (nid,)).fetchone()
    issues = published_newsletters(conn) if issue else []
    conn.close()
    return issue, issues


@app.route('/newsletter/<int:nid>')
def newsletter_issue(nid):
    issue, issues = _published_issue(nid)
    if not issue:
        return redirect(url_for('newsletter'))
    track('newsletter')
    return _render_issue(issue, 'cover', issues)


@app.route('/newsletter/<int:nid>/full')
def newsletter_full(nid):
    issue, issues = _published_issue(nid)
    if not issue:
        return redirect(url_for('newsletter'))
    if not issue['full_pdf']:
        return redirect(url_for('newsletter_issue', nid=nid))
    track('newsletter_full')
    return _render_issue(issue, 'full', issues)


# ---- admin ----------------------------------------------------------------------------------

def _issue_form_values():
    return {
        'title': request.form.get('title', '').strip()[:120],
        'issue_label': request.form.get('issue_label', '').strip()[:80],
        'full_title': request.form.get('full_title', '').strip()[:120],
        'issue_date': request.form.get('issue_date', '').strip()[:10],
        'is_published': 1 if request.form.get('is_published') else 0,
    }


@app.route('/admin/newsletter')
@login_required
def admin_newsletter():
    conn = get_db()
    issues = conn.execute("SELECT * FROM newsletters ORDER BY issue_date DESC, id DESC").fetchall()
    card = conn.execute("SELECT id, is_active FROM hub_cards WHERE slug='volunteer-newsletter'").fetchone()
    conn.close()
    return render_template('admin/newsletter.html', issues=issues, hub_card=card, pages_of=newsletter_pages)


@app.route('/admin/newsletter/new', methods=['GET', 'POST'])
@app.route('/admin/newsletter/<int:nid>/edit', methods=['GET', 'POST'])
@login_required
def admin_newsletter_form(nid=None):
    conn = get_db()
    issue = conn.execute("SELECT * FROM newsletters WHERE id=?", (nid,)).fetchone() if nid else None
    if nid and not issue:
        conn.close()
        return redirect(url_for('admin_newsletter'))
    if request.method == 'POST':
        values = _issue_form_values()
        old = {part: (issue[f'{part}_pdf'], newsletter_pages(issue, part)) if issue else ('', [])
               for part in ('cover', 'full')}
        new, errors = {}, []
        for part in ('cover', 'full'):
            pdf, pages, error = save_newsletter_pdf(request.files.get(f'{part}_file'))
            if error:
                errors.append(f"{'Cover' if part == 'cover' else 'Full newsletter'}: {error}")
            elif pdf:
                new[part] = (pdf, pages)
        reuse = None
        if not errors and not new.get('cover') and not old['cover'][0]:
            reuse = current_cover(conn)
            if not reuse:
                errors.append('Please choose the cover PDF.')
        if errors:
            for pdf, pages in new.values():
                remove_newsletter_files(pdf, pages)
            cover = None if issue else current_cover(conn)
            conn.close()
            for e in errors:
                flash(e, 'info')
            return render_template('admin/newsletter_form.html', issue=issue, form=values, pages_of=newsletter_pages,
                                   cover=cover)
        remove_full = request.form.get('remove_full') and 'full' not in new
        for part, (pdf, pages) in new.items():
            values[f'{part}_pdf'], values[f'{part}_pages'] = pdf, json.dumps(pages)
            values[f'{part}_text'] = pdf_text_html(_abs_upload(pdf))
        if reuse:
            for col in ('cover_pdf', 'cover_pages', 'cover_text'):
                values[col] = reuse[col]
        if remove_full:
            values['full_pdf'], values['full_pages'], values['full_text'] = '', '[]', ''
        if not values['title']:
            values['title'] = values['issue_label'] or 'Volunteer Newsletter'
        if not values['issue_date']:
            values['issue_date'] = issue['issue_date'] if issue else date.today().isoformat()
        cols = list(values)
        if issue:
            conn.execute(f"UPDATE newsletters SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                         [values[c] for c in cols] + [nid])
        else:
            nid = conn.execute(f"INSERT INTO newsletters ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [values[c] for c in cols]).lastrowid
        conn.commit()
        conn.close()
        for part in new:
            remove_newsletter_files(*old[part])
        if remove_full:
            remove_newsletter_files(*old['full'])
        flash('Newsletter saved!', 'success')
        return redirect(url_for('admin_newsletter'))
    cover = None if issue else current_cover(conn)
    conn.close()
    if issue:
        form = dict(issue)
    else:   # a new month: same cover and names as last time, new date
        form = {'title': cover['title'] if cover else '', 'full_title': cover['full_title'] if cover else '',
                'issue_label': '', 'issue_date': date.today().isoformat(), 'is_published': 1}
    return render_template('admin/newsletter_form.html', issue=issue, form=form, pages_of=newsletter_pages,
                           cover=cover)


@app.route('/admin/newsletter/<int:nid>/delete', methods=['POST'])
@login_required
def admin_newsletter_delete(nid):
    conn = get_db()
    issue = conn.execute("SELECT * FROM newsletters WHERE id=?", (nid,)).fetchone()
    if issue:
        conn.execute("DELETE FROM newsletters WHERE id=?", (nid,))
        conn.commit()
        for part in ('cover', 'full'):
            remove_newsletter_files(issue[f'{part}_pdf'], newsletter_pages(issue, part))
        flash('Newsletter deleted.', 'info')
    conn.close()
    return redirect(url_for('admin_newsletter'))
