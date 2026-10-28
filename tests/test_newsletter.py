import io
import json
import os

import pytest

import newsletter


def make_pdf(pages=1, link=None, text=None):
    """A tiny hand-written PDF: `pages` US-letter pages, the first one carrying a web link and,
    when given, `text`: lines of (size, x, y, string) in Helvetica."""
    objs = ['<< /Type /Catalog /Pages 2 0 R >>', None]
    kids = []
    for i in range(pages):
        extra = ''
        if text and i == 0:
            body = ''.join(f'BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n' for size, x, y, s in text)
            objs.append(f'<< /Length {len(body)} >>\nstream\n{body}endstream')
            objs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
            extra += f' /Contents {len(objs) - 1} 0 R /Resources << /Font << /F1 {len(objs)} 0 R >> >>'
        if link and i == 0:
            objs.append(f'<< /Type /Annot /Subtype /Link /Rect [100 600 300 650] /Border [0 0 0] '
                        f'/A << /S /URI /URI ({link}) >> >>')
            extra += f' /Annots [{len(objs)} 0 R]'
        objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]{extra} >>')
        kids.append(f'{len(objs)} 0 R')
    objs[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {pages} >>'
    out, offsets = b'%PDF-1.4\n', []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f'{n} 0 obj\n{body}\nendobj\n'.encode()
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return out


STORY = [(24, 72, 700, 'The Nose of a Camel'),
         (11, 72, 660, 'There was a story of a man traveling through the desert. He noticed'),
         (11, 72, 646, 'that it was getting late and needed to bed down for the night.')]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(newsletter, 'UPLOAD_FOLDER', str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def clean(db):
    db.execute("DELETE FROM newsletters")
    db.commit()
    yield


def upload(admin, nid=None, cover=None, full=None, **fields):
    data = {'title': 'The Nose of a Camel', 'issue_label': '#1 NOV 2026', 'full_title': 'The Table Leaf',
            'issue_date': '2026-11-01', 'is_published': '1', **fields}
    if cover is not None:
        data['cover_file'] = (io.BytesIO(cover), 'Nose_of_a_camel.PDF')
    if full is not None:
        data['full_file'] = (io.BytesIO(full), 'downloadfile.PDF')
    url = f'/admin/newsletter/{nid}/edit' if nid else '/admin/newsletter/new'
    return admin.post(url, data, content_type='multipart/form-data')


def latest(db):
    return db.execute("SELECT * FROM newsletters ORDER BY id DESC LIMIT 1").fetchone()


def test_hub_shows_the_volunteer_newsletter_card(client):
    html = client.get('/hub').get_data(as_text=True)
    assert 'Volunteer Newsletter' in html and '/newsletter' in html


def test_empty_newsletter_page_says_coming_soon(client):
    html = client.get('/newsletter').get_data(as_text=True)
    assert 'on its way' in html


def test_upload_renders_every_page_and_the_cover_opens_the_full_newsletter(admin, client, db, folder):
    r = upload(admin, cover=make_pdf(1), full=make_pdf(2, link='https://example.com/devotion'))
    assert r.status_code == 302
    row = latest(db)
    cover, full = json.loads(row['cover_pages']), json.loads(row['full_pages'])
    assert len(cover) == 1 and len(full) == 2
    for p in cover + full:
        assert os.path.exists(folder / p['src'].split('/', 1)[1])
        assert p['w'] == newsletter.NEWSLETTER_PAGE_WIDTH and p['h'] > p['w']        # portrait, like the PDF
    assert (folder / row['cover_pdf'].split('/', 1)[1]).read_bytes().startswith(b'%PDF-')

    html = client.get('/newsletter').get_data(as_text=True)
    assert cover[0]['src'] in html and full[0]['src'] not in html                  # the cover only
    assert f'/newsletter/{row["id"]}/full' in html and "Read this month&#39;s issue" in html
    full_html = client.get(f'/newsletter/{row["id"]}/full').get_data(as_text=True)
    assert all(p['src'] in full_html for p in full) and row['full_pdf'] in full_html  # pages + download


def test_links_in_the_pdf_stay_tappable(admin, client, db, folder):
    upload(admin, cover=make_pdf(1), full=make_pdf(1, link='https://example.com/devotion'))
    link = json.loads(latest(db)['full_pages'])[0]['links'][0]
    assert link['url'] == 'https://example.com/devotion'
    assert (link['x'], link['w']) == (round(100 * 100 / 612, 2), round(100 * 200 / 612, 2))
    assert link['y'] == round(100 * (792 - 650) / 792, 2)                             # measured from the top
    html = client.get(f'/newsletter/{latest(db)["id"]}/full').get_data(as_text=True)
    assert 'href="https://example.com/devotion"' in html


def test_cover_only_issue_has_no_read_more_button(admin, client, db, folder):
    upload(admin, cover=make_pdf(1))
    nid = latest(db)['id']
    assert "this month&#39;s issue" not in client.get('/newsletter').get_data(as_text=True)
    assert client.get(f'/newsletter/{nid}/full').status_code == 302


def test_cover_is_required_and_only_pdfs_are_accepted(admin, db, folder):
    upload(admin, full=make_pdf(1))
    assert latest(db) is None
    r = upload(admin, cover=b'not a pdf at all')
    assert r.status_code == 200 and 'Please upload a PDF' in r.get_data(as_text=True)
    assert latest(db) is None and not list(folder.iterdir())                          # nothing left behind


def test_too_many_pages_is_refused(admin, db, folder, monkeypatch):
    monkeypatch.setattr(newsletter, 'NEWSLETTER_MAX_PAGES', 2)
    r = upload(admin, cover=make_pdf(3))
    assert 'allows up to 2' in r.get_data(as_text=True) and latest(db) is None
    assert not list(folder.iterdir())


def test_replacing_a_pdf_removes_the_old_files(admin, db, folder):
    upload(admin, cover=make_pdf(1), full=make_pdf(2))
    row = latest(db)
    old_full = [row['full_pdf']] + [p['src'] for p in json.loads(row['full_pages'])]
    upload(admin, nid=row['id'], full=make_pdf(1))
    row2 = latest(db)
    assert row2['cover_pdf'] == row['cover_pdf'] and len(json.loads(row2['full_pages'])) == 1
    assert not any(os.path.exists(folder / f.split('/', 1)[1]) for f in old_full)

    upload(admin, nid=row['id'], remove_full='1')
    assert latest(db)['full_pdf'] == '' and latest(db)['full_pages'] == '[]'


def test_hidden_issues_are_not_shown(admin, client, db, folder):
    upload(admin, cover=make_pdf(1), title='Old issue', issue_date='2026-10-01')
    upload(admin, cover=make_pdf(1), title='Hidden issue', issue_date='2026-11-01', is_published='')
    hidden = latest(db)['id']
    html = client.get('/newsletter').get_data(as_text=True)
    assert 'Old issue' in html and 'Hidden issue' not in html
    assert client.get(f'/newsletter/{hidden}').status_code == 302


def test_newest_issue_comes_first_and_past_issues_are_listed(admin, client, db, folder):
    upload(admin, cover=make_pdf(1), issue_label='October issue', issue_date='2026-10-01')
    upload(admin, cover=make_pdf(1), issue_label='November issue', issue_date='2026-11-01')
    html = client.get('/newsletter').get_data(as_text=True)
    assert html.index('November issue') < html.index('October issue') and 'Past issues' in html


def test_delete_removes_the_issue_and_its_files(admin, db, folder):
    upload(admin, cover=make_pdf(1), full=make_pdf(2))
    nid = latest(db)['id']
    admin.post(f'/admin/newsletter/{nid}/delete')
    assert latest(db) is None and not list(folder.iterdir())


def test_admin_pages_need_login(client):
    assert client.get('/admin/newsletter').status_code == 302
    assert client.post('/admin/newsletter/new', {}).status_code in (302, 403)


def test_text_only_view_reads_the_pdf_text(admin, client, db, folder):
    upload(admin, cover=make_pdf(1, text=STORY), full=make_pdf(1, text=[(20, 72, 700, 'Hey There'),
           (11, 72, 660, 'I lived with my grandmother growing up and helped get the house ready.')],
           link='https://example.com/devotion'))
    row = latest(db)
    assert 'The Nose of a Camel' in row['cover_text'] and 'Hey There' in row['full_text']
    html = client.get('/newsletter').get_data(as_text=True)
    assert 'Text only' in html and 'Original' in html
    text = client.get(f'/newsletter/{row["id"]}?view=text').get_data(as_text=True)
    assert 'needed to bed down for the night' in text and '<article class="text">' in text
    assert f'/newsletter/{row["id"]}/full?view=text' in text                 # the button stays in text mode
    full = client.get(f'/newsletter/{row["id"]}/full?view=text').get_data(as_text=True)
    assert 'grandmother' in full and 'https://example.com/devotion' in full   # links listed under the text


def test_a_pdf_without_text_has_no_text_switch(admin, client, db, folder):
    upload(admin, cover=make_pdf(1))
    assert latest(db)['cover_text'] == ''
    html = client.get(f'/newsletter/{latest(db)["id"]}?view=text').get_data(as_text=True)
    assert 'Text only' not in html and 'class="page"' in html                   # falls back to the original


def test_issues_uploaded_before_text_mode_are_read_on_first_view(admin, client, db, folder):
    upload(admin, cover=make_pdf(1, text=STORY))
    nid = latest(db)['id']
    db.execute("UPDATE newsletters SET cover_text=NULL WHERE id=?", (nid,))
    db.commit()
    assert 'traveling through the desert' in client.get(f'/newsletter/{nid}?view=text').get_data(as_text=True)
    assert 'traveling' in latest(db)['cover_text']


def test_letter_spaced_headings_are_joined_into_words():
    def word(t, x0):
        return {'text': t, 'x0': x0, 'x1': x0 + 6, 'top': 10}
    x, words = 0, []
    for ch in 'THE NOSE':
        if ch == ' ':
            x += 14
            continue
        words.append(word(ch, x))
        x += 10
    assert [w['text'] for w in newsletter._join_spaced_letters(words)] == ['THE', 'NOSE']
    normal = [word('Hello', 0), word('there', 40)]
    assert newsletter._join_spaced_letters(normal) == normal


def test_new_month_reuses_the_cover_and_only_needs_the_new_pdf(admin, client, db, folder):
    upload(admin, cover=make_pdf(1, text=STORY), full=make_pdf(2), issue_date='2026-11-01', issue_label='#1 NOV 2026')
    first = latest(db)
    form = admin.get('/admin/newsletter/new').get_data(as_text=True)
    assert 'Leave empty to use it again' in form and 'value="The Table Leaf"' in form   # names carried over
    assert upload(admin, full=make_pdf(1), issue_date='2026-12-01', issue_label='#2 DEC 2026').status_code == 302
    second = latest(db)
    assert second['id'] != first['id']
    assert (second['cover_pdf'], second['cover_pages'], second['cover_text']) == \
        (first['cover_pdf'], first['cover_pages'], first['cover_text'])
    assert second['full_pdf'] != first['full_pdf']
    html = client.get('/newsletter').get_data(as_text=True)
    assert json.loads(first['cover_pages'])[0]['src'] in html and '#2 DEC 2026' in html
    assert f'/newsletter/{second["id"]}/full' in html


def test_deleting_a_month_keeps_the_shared_cover(admin, db, folder):
    upload(admin, cover=make_pdf(1), full=make_pdf(1), issue_date='2026-11-01')
    upload(admin, full=make_pdf(1), issue_date='2026-12-01')
    second = latest(db)
    cover_files = [second['cover_pdf']] + [p['src'] for p in json.loads(second['cover_pages'])]
    admin.post(f'/admin/newsletter/{second["id"]}/delete')
    assert all(os.path.exists(folder / f.split('/', 1)[1]) for f in cover_files)
    assert not os.path.exists(folder / second['full_pdf'].split('/', 1)[1])
