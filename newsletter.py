"""Volunteer Newsletter (/newsletter) and its admin page (/admin/newsletter).

Each issue is two PDFs: a one-page COVER (e.g. "The Nose of a Camel") that the newsletter page
shows, and the FULL newsletter (e.g. "The Table Leaf") that opens when the cover is tapped.
Pages are rendered to JPEGs once, at upload, so phones see the PDF exactly as it was designed
(fonts, colours, layout) without a PDF plug-in; links inside the PDF stay tappable on top of
the picture. The original PDF is kept for Download.
"""
from core import *
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


def remove_newsletter_files(pdf, pages):
    for rel in [pdf] + [p.get('src', '') for p in pages]:
        if rel and rel.startswith('uploads/') and '/' not in rel[len('uploads/'):]:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, rel[len('uploads/'):]))
            except OSError:
                pass


def newsletter_pages(row, part):
    try:
        return json.loads(row[f'{part}_pages'] or '[]')
    except (TypeError, ValueError):
        return []


def published_newsletters(conn):
    return conn.execute("SELECT * FROM newsletters WHERE is_published=1 AND cover_pdf<>'' "
                        "ORDER BY issue_date DESC, id DESC").fetchall()


# ---- public pages ---------------------------------------------------------------------------

def _render_issue(issue, part, issues):
    pages = newsletter_pages(issue, part)
    return render_template('newsletter.html', settings=all_settings(), issue=issue, part=part, pages=pages,
                           has_full=bool(issue['full_pdf']), issues=issues, pages_of=newsletter_pages)


@app.route('/newsletter')
def newsletter():
    track('newsletter')
    conn = get_db()
    issues = published_newsletters(conn)
    conn.close()
    if not issues:
        return render_template('newsletter.html', settings=all_settings(), issue=None, part='cover', pages=[],
                               has_full=False, issues=[])
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
        if not errors and not new.get('cover') and not old['cover'][0]:
            errors.append('Please choose the cover PDF.')
        if errors:
            for pdf, pages in new.values():
                remove_newsletter_files(pdf, pages)
            conn.close()
            for e in errors:
                flash(e, 'info')
            return render_template('admin/newsletter_form.html', issue=issue, form=values, pages_of=newsletter_pages)
        remove_full = request.form.get('remove_full') and 'full' not in new
        for part, (pdf, pages) in new.items():
            values[f'{part}_pdf'], values[f'{part}_pages'] = pdf, json.dumps(pages)
        if remove_full:
            values['full_pdf'], values['full_pages'] = '', '[]'
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
    conn.close()
    form = dict(issue) if issue else {'title': '', 'issue_label': '', 'full_title': '',
                                      'issue_date': date.today().isoformat(), 'is_published': 1}
    return render_template('admin/newsletter_form.html', issue=issue, form=form, pages_of=newsletter_pages)


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
