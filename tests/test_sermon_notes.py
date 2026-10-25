"""Sermon notes: hook title vs. sermon title, series parts, the intro's paragraphs, PDF glyph cleanup."""
import documents


def _note(db, **cols):
    cols = dict({'title': 'Hook', 'note_date': '2026-10-18', 'summary': '', 'body_html': '<p>x</p>'}, **cols)
    db.execute(f"INSERT INTO sermon_notes({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
    db.commit()
    return db.execute("SELECT MAX(id) FROM sermon_notes").fetchone()[0]


def test_byline_joins_sermon_title_and_part():
    assert documents.sermon_byline({'sermon_title': 'The Towel & The Basin', 'series_part': '1'}) == 'The Towel & The Basin · Pt 1'
    assert documents.sermon_byline({'series_name': 'Servant', 'series_part': '2'}) == 'Servant · Pt 2'
    assert documents.sermon_byline({'sermon_title': '', 'series_part': '3'}) == ''


def test_cid_glyphs_are_stripped():
    assert documents.strip_cid_glyphs('He (cid:0) had come from God') == 'He had come from God'


def test_detail_shows_sermon_title_in_place_of_date_and_renders_intro_paragraphs(client, db):
    nid = _note(db, title='A title never replaces a towel.', sermon_title='The Towel & The Basin',
                summary='<p>You never become so gifted.</p>')
    html = client.get(f'/sermon-notes/{nid}').get_data(as_text=True)
    assert 'The Towel &amp; The Basin' in html
    assert 'Sermon Notes · OCT 18' in html and '2026-10-18' not in html
    assert '<p>You never become so gifted.</p>' in html and '&lt;p&gt;' not in html


def test_series_parts_link_to_each_other(client, db):
    one = _note(db, sermon_title='Part One Talk', series_name='Servant Heart', series_part='1', note_date='2026-10-11')
    two = _note(db, sermon_title='Part Two Talk', series_name='Servant Heart', series_part='2')
    html = client.get(f'/sermon-notes/{two}').get_data(as_text=True)
    assert 'Part Two Talk · Pt 2' in html and 'Servant Heart' in html
    assert f'/sermon-notes/{one}' in html and html.index('Part One Talk') < html.index('Part Two Talk</span>')


def test_admin_saves_sermon_title_and_series(admin, db):
    nid = _note(db)
    admin.post(f'/admin/sermon-notes/{nid}/edit', {
        'title': 'Hook', 'note_date': '2026-10-18', 'summary': '', 'body_html': '<p>x</p>',
        'sermon_title': 'The Towel & The Basin', 'series_name': 'Servant Heart', 'series_part': 'Pt 3'})
    row = db.execute("SELECT sermon_title, series_name, series_part FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    assert tuple(row) == ('The Towel & The Basin', 'Servant Heart', '3')


def _word_style_pdf(path):
    """Mimics a Word export: "•" bullets that pdfplumber reads as "(cid:127)", Courier "o"
    sub-bullets sitting a point below the text, and a sub-bullet that wraps."""
    canvas = __import__('pytest').importorskip('reportlab.pdfgen.canvas')
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    y = 720

    def line(text, x=72, marker=None, marker_x=None):
        nonlocal y
        if marker:
            c.setFont('Courier' if marker == 'o' else 'Helvetica', 11)
            c.drawString(marker_x, y - 1.2, marker)
        c.setFont('Helvetica', 11)
        c.drawString(x, y, text)
        y -= 15

    line('Today the sermon comes from a song Emma just released')
    line('The song acts as a prayer and invitation for all', x=90, marker='•', marker_x=76)
    line('Whoever has ears come hear the good news', x=126, marker='o', marker_x=108)
    line('The lyrics she writes come with amazing daddy and daughter theological', x=126, marker='o', marker_x=108)
    line('convos', x=126)
    line('Jesus taught seven parables in Matthew 13')
    c.save()


def test_pdf_word_bullets_nest_and_wrapped_items_rejoin(tmp_path):
    pdf = tmp_path / 'notes.pdf'
    _word_style_pdf(pdf)
    html = documents.extract_pdf_html(str(pdf))
    assert ' o<' not in html and 'cid' not in html and 'glyph' not in html
    assert ('<ul><li>The song acts as a prayer and invitation for all'
            '<ul><li>Whoever has ears come hear the good news</li>'
            '<li>The lyrics she writes come with amazing daddy and daughter theological convos</li></ul></li></ul>') in html
    assert '<p>Today the sermon comes from a song Emma just released</p>' in html
    assert '<p>Jesus taught seven parables in Matthew 13</p>' in html


def test_import_does_not_copy_the_first_line_into_the_intro(tmp_path, monkeypatch):
    (tmp_path / 'static').mkdir()
    _word_style_pdf(tmp_path / 'static' / 'notes.pdf')
    monkeypatch.setattr(documents, 'BASE_DIR', str(tmp_path))
    payload = documents.build_sermon_note_payload('notes.pdf', 'pdf', fallback_title='Hook')
    assert payload['summary'] == '' and 'Today the sermon comes' in payload['body_html']


def test_detail_hides_an_intro_that_repeats_the_first_line(client, db):
    nid = _note(db, summary='My daughter Emma writes songs',
                body_html='<p>My daughter Emma writes songs</p><p>Next line</p>')
    html = client.get(f'/sermon-notes/{nid}').get_data(as_text=True)
    assert html.count('My daughter Emma writes songs') == 1
