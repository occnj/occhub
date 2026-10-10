"""Admin: content management (hub cards, sermon notes, missions, beyond the walls, leaders, crew, beliefs, values, ministries, serve, events)."""
from core import *
from youtube import *
from documents import *

@app.route('/admin/hub-cards')
@login_required
def admin_hub_cards():
    conn = get_db()
    cards = conn.execute(
        "SELECT c.*, p.title AS parent_title FROM hub_cards c LEFT JOIN hub_cards p ON p.id = c.parent_id ORDER BY COALESCE(c.parent_id, c.id), c.sort_order, c.id"
    ).fetchall()
    conn.close()
    return render_template('admin/hub_cards.html', cards=cards)

@app.route('/admin/hub-cards/new', methods=['GET', 'POST'])
@login_required
def admin_hub_card_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f'card-{uuid.uuid4().hex[:6]}'
        photo = save_upload('photo') or ''
        modal_image = save_upload('modal_image') or ''
        raw_parent = (request.form.get('parent_id') or '').strip()
        parent_id = int(raw_parent) if raw_parent else None
        conn = get_db()
        card_notice_image = save_upload('card_notice_image') or ''
        conn.execute(
            "INSERT INTO hub_cards (slug,title,subtitle,photo,icon,card_type,parent_id,target_url,media_url,modal_title,modal_body,modal_button_label,modal_button_url,modal_image,open_in_new_tab,sort_order,is_active,card_notice_enabled,card_notice_block,card_notice_title,card_notice_body,card_notice_link,card_notice_link_label,card_notice_image) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                slug,
                title,
                request.form.get('subtitle', '').strip(),
                photo,
                request.form.get('icon', 'bi-grid-fill').strip(),
                request.form.get('card_type', 'link').strip(),
                parent_id,
                request.form.get('target_url', '').strip(),
                request.form.get('media_url', '').strip(),
                request.form.get('modal_title', '').strip(),
                request.form.get('modal_body', '').strip(),
                request.form.get('modal_button_label', '').strip(),
                request.form.get('modal_button_url', '').strip(),
                modal_image,
                1 if request.form.get('open_in_new_tab') else 0,
                form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'hub_cards'),
                1 if request.form.get('is_active') else 0,
                1 if request.form.get('card_notice_enabled') else 0,
                1 if request.form.get('card_notice_block') else 0,
                request.form.get('card_notice_title', '').strip(),
                request.form.get('card_notice_body', '').strip(),
                request.form.get('card_notice_link', '').strip(),
                request.form.get('card_notice_link_label', '').strip() or 'Learn More',
                card_notice_image,
            )
        )
        conn.commit()
        conn.close()
        flash('Hub card added!', 'success')
        return redirect(url_for('admin_hub_cards'))
    conn = get_db()
    parent_choices = conn.execute("SELECT id,title FROM hub_cards WHERE parent_id IS NULL ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template('admin/hub_card_form.html', card=None, parent_choices=parent_choices)

@app.route('/admin/hub-cards/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def admin_hub_card_edit(cid):
    conn = get_db()
    card = conn.execute("SELECT * FROM hub_cards WHERE id=?", (cid,)).fetchone()
    if not card:
        conn.close()
        return redirect(url_for('admin_hub_cards'))
    parent_choices = conn.execute("SELECT id,title FROM hub_cards WHERE parent_id IS NULL AND id != ? ORDER BY sort_order, id", (cid,)).fetchall()
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = card['photo']
        modal_image = save_upload('modal_image')
        if modal_image is None:
            modal_image = card['modal_image']
        raw_parent = (request.form.get('parent_id') or '').strip()
        parent_id = int(raw_parent) if raw_parent else None
        card_notice_image = save_upload('card_notice_image')
        if card_notice_image is None:
            card_notice_image = card['card_notice_image'] if card['card_notice_image'] else ''
        conn.execute(
            "UPDATE hub_cards SET title=?,subtitle=?,photo=?,icon=?,card_type=?,parent_id=?,target_url=?,media_url=?,modal_title=?,modal_body=?,modal_button_label=?,modal_button_url=?,modal_image=?,open_in_new_tab=?,sort_order=?,is_active=?,card_notice_enabled=?,card_notice_block=?,card_notice_title=?,card_notice_body=?,card_notice_link=?,card_notice_link_label=?,card_notice_image=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('subtitle', '').strip(),
                photo,
                request.form.get('icon', 'bi-grid-fill').strip(),
                request.form.get('card_type', 'link').strip(),
                parent_id,
                request.form.get('target_url', '').strip(),
                request.form.get('media_url', '').strip(),
                request.form.get('modal_title', '').strip(),
                request.form.get('modal_body', '').strip(),
                request.form.get('modal_button_label', '').strip(),
                request.form.get('modal_button_url', '').strip(),
                modal_image,
                1 if request.form.get('open_in_new_tab') else 0,
                form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'hub_cards'),
                1 if request.form.get('is_active') else 0,
                1 if request.form.get('card_notice_enabled') else 0,
                1 if request.form.get('card_notice_block') else 0,
                request.form.get('card_notice_title', '').strip(),
                request.form.get('card_notice_body', '').strip(),
                request.form.get('card_notice_link', '').strip(),
                request.form.get('card_notice_link_label', '').strip() or 'Learn More',
                card_notice_image,
                cid,
            )
        )
        conn.commit()
        flash('Hub card updated!', 'success')
        card = conn.execute("SELECT * FROM hub_cards WHERE id=?", (cid,)).fetchone()
    conn.close()
    return render_template('admin/hub_card_form.html', card=card, parent_choices=parent_choices)

@app.route('/admin/hub-cards/<int:cid>/delete', methods=['POST'])
@login_required
def admin_hub_card_delete(cid):
    conn = get_db()
    conn.execute("DELETE FROM hub_cards WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    flash('Hub card removed.', 'info')
    return redirect(url_for('admin_hub_cards'))

@app.route('/admin/sermon-notes')
@login_required
def admin_sermon_notes():
    conn = get_db()
    notes = conn.execute("SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC").fetchall()
    ns_counts = next_steps_counts(conn)
    conn.close()
    return render_template('admin/sermon_notes.html', notes=notes, ns_counts=ns_counts)

def next_steps_heading_fields(note=None):
    """The note-level heading and instruction for the Oasis Next Steps section.
    A submit that never rendered the homework panel must not silently wipe them,
    so an absent field keeps whatever is already stored."""
    def field(name):
        if name not in request.form:
            return row_value(note, name)
        return request.form.get(name, '').strip()

    return field('next_steps_title'), field('next_steps_note')

def sync_next_steps(conn, nid):
    """Apply the Add Homework panel to one note: rename or drop the documents
    already attached, then append whatever was uploaded this time."""
    kept = 0
    for item in note_next_steps(conn, nid):
        if request.form.get(f"ns_remove_{item['id']}") == '1':
            conn.execute("DELETE FROM sermon_next_steps WHERE id=? AND note_id=?", (item['id'], nid))
            continue
        title_key = f"ns_title_{item['id']}"
        if title_key in request.form:
            conn.execute(
                "UPDATE sermon_next_steps SET title=?, sort_order=? WHERE id=? AND note_id=?",
                (request.form.get(title_key, '').strip(), kept, item['id'], nid)
            )
        else:
            conn.execute("UPDATE sermon_next_steps SET sort_order=? WHERE id=? AND note_id=?",
                         (kept, item['id'], nid))
        kept += 1

    for f in request.files.getlist('next_steps_file'):
        if not f or not f.filename:
            continue
        if kept >= MAX_NEXT_STEPS_FILES:
            flash(f'A sermon note can hold {MAX_NEXT_STEPS_FILES} Next Steps files — '
                  f'"{f.filename}" and anything after it were skipped.', 'info')
            break
        stored, _ext = save_document_file(f)
        if not stored:
            flash(f'"{f.filename}" was skipped — only PDF and DOCX files can be attached.', 'info')
            continue
        conn.execute(
            "INSERT INTO sermon_next_steps (note_id, file, title, sort_order) VALUES (?,?,?,?)",
            (nid, stored, sermon_title_from_filename(f.filename), kept)
        )
        kept += 1
    return kept

@app.route('/admin/sermon-notes/new', methods=['GET', 'POST'])
@login_required
def admin_sermon_note_new():
    if request.method == 'POST':
        source_file, ext = save_document_upload('source_file')
        if not source_file:
            flash('Please upload a PDF or DOCX file.', 'info')
            return render_template('admin/sermon_note_form.html', note=None)
        note_date = request.form.get('note_date', '').strip() or date.today().isoformat()
        payload = build_sermon_note_payload(
            source_file,
            ext,
            fallback_title=request.form.get('title', '').strip(),
            summary=request.form.get('summary', '').strip(),
        )
        ns_title, ns_note = next_steps_heading_fields()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO sermon_notes (title,note_date,summary,body_html,source_file,"
            "next_steps_title,next_steps_note) VALUES (?,?,?,?,?,?,?)",
            (payload['title'], note_date, payload['summary'], payload['body_html'], payload['source_file'],
             ns_title, ns_note)
        )
        sync_next_steps(conn, cur.lastrowid)
        conn.commit()
        conn.close()
        flash('Sermon notes imported!', 'success')
        return redirect(url_for('admin_sermon_notes'))
    return render_template('admin/sermon_note_form.html', note=None, ns_items=[])

@app.route('/admin/sermon-notes/<int:nid>/edit', methods=['GET', 'POST'])
@login_required
def admin_sermon_note_edit(nid):
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    if not note:
        conn.close()
        return redirect(url_for('admin_sermon_notes'))
    if request.method == 'POST':
        source_file, ext = save_document_upload('source_file')
        if source_file:
            payload = build_sermon_note_payload(
                source_file,
                ext,
                fallback_title=request.form.get('title', '').strip(),
                summary=request.form.get('summary', '').strip(),
            )
            title = payload['title']
            summary = payload['summary']
            body_html = payload['body_html']
            stored_file = payload['source_file']
        else:
            title = request.form.get('title', '').strip() or note['title']
            summary = request.form.get('summary', '').strip()
            body_html = request.form.get('body_html', '').strip()
            stored_file = note['source_file']
        ns_title, ns_note = next_steps_heading_fields(note)
        conn.execute(
            "UPDATE sermon_notes SET title=?, note_date=?, summary=?, body_html=?, source_file=?,"
            " next_steps_title=?, next_steps_note=? WHERE id=?",
            (
                title,
                request.form.get('note_date', '').strip() or note['note_date'],
                summary,
                body_html,
                stored_file,
                ns_title,
                ns_note,
                nid,
            )
        )
        sync_next_steps(conn, nid)
        conn.commit()
        updated = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
        ns_items = note_next_steps(conn, nid)
        conn.close()
        flash('Sermon notes updated!', 'success')
        return render_template('admin/sermon_note_form.html', note=updated, ns_items=ns_items)
    ns_items = note_next_steps(conn, nid)
    conn.close()
    return render_template('admin/sermon_note_form.html', note=note, ns_items=ns_items)

@app.route('/admin/sermon-notes/<int:nid>/delete', methods=['POST'])
@login_required
def admin_sermon_note_delete(nid):
    conn = get_db()
    conn.execute("DELETE FROM sermon_notes WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    flash('Sermon note removed.', 'info')
    return redirect(url_for('admin_sermon_notes'))

@app.route('/admin/hub-notice', methods=['GET', 'POST'])
@login_required
def admin_hub_notice():
    if request.method == 'POST':
        conn = get_db()
        values = {
            'hub_notice_enabled': '1' if request.form.get('hub_notice_enabled') else '0',
            'hub_notice_title': request.form.get('hub_notice_title', '').strip(),
            'hub_notice_body': request.form.get('hub_notice_body', '').strip(),
            'hub_notice_link': request.form.get('hub_notice_link', '').strip(),
            'hub_notice_link_label': request.form.get('hub_notice_link_label', '').strip() or 'Learn More',
        }
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        image = save_upload('hub_notice_image_file')
        if image:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('hub_notice_image',?)", (image,))
        conn.commit()
        conn.close()
        flash('Hub notice updated!', 'success')
        return redirect(url_for('admin_hub_notice'))
    return render_template('admin/hub_notice.html', settings=all_settings())

@app.route('/admin/get-involved', methods=['GET', 'POST'])
@login_required
def admin_get_involved():
    if request.method == 'POST':
        conn = get_db()
        values = {'get_involved_layout': 'row' if request.form.get('get_involved_layout') == 'row' else 'stack'}
        for slot in range(1, GET_INVOLVED_SLOTS + 1):
            form = lambda field: request.form.get(get_involved_key(slot, field), '').strip()
            values[get_involved_key(slot, 'enabled')] = '1' if form('enabled') else '0'
            values[get_involved_key(slot, 'title')] = form('title')
            values[get_involved_key(slot, 'body')] = form('body')
            values[get_involved_key(slot, 'link')] = form('link')
            values[get_involved_key(slot, 'link_label')] = form('link_label') or 'Sign Up'
            values[get_involved_key(slot, 'icon')] = form('icon') or 'bi-people-fill'
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        conn.commit()
        conn.close()
        flash('Get Involved cards updated!', 'success')
        return redirect(url_for('admin_get_involved'))
    return render_template('admin/get_involved.html', settings=all_settings(),
                           slots=range(1, GET_INVOLVED_SLOTS + 1), gi_key=get_involved_key)

@app.route('/admin/missions')
@login_required
def admin_missions():
    conn = get_db()
    missions = get_mission_cards(conn)
    conn.close()
    return render_template('admin/missions.html', missions=missions)

@app.route('/admin/missions/new', methods=['GET', 'POST'])
@login_required
def admin_mission_new():
    if request.method == 'POST':
        cover = save_upload('cover_photo') or ''
        conn = get_db()
        conn.execute(
            "INSERT INTO missions (title,summary,body,cover_photo,sort_order) VALUES (?,?,?,?,?)",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'missions'),
            )
        )
        conn.commit()
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        flash('Mission created! Add images below.', 'success')
        return redirect(url_for('admin_mission_edit', mid=mid))
    return render_template('admin/mission_form.html', mission=None, images=[])

@app.route('/admin/missions/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_mission_edit(mid):
    conn = get_db()
    mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('admin_missions'))
    if request.method == 'POST':
        cover = save_upload('cover_photo')
        if cover is None:
            cover = mission['cover_photo']
        conn.execute(
            "UPDATE missions SET title=?,summary=?,body=?,cover_photo=?,sort_order=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                form_sort_order() if form_sort_order() is not None else mission['sort_order'],
                mid,
            )
        )
        conn.commit()
        flash('Mission updated!', 'success')
        mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    images = conn.execute(
        "SELECT * FROM mission_images WHERE mission_id=? ORDER BY sort_order, id",
        (mid,)
    ).fetchall()
    conn.close()
    return render_template('admin/mission_form.html', mission=mission, images=images)

@app.route('/admin/missions/<int:mid>/delete', methods=['POST'])
@login_required
def admin_mission_delete(mid):
    conn = get_db()
    conn.execute("DELETE FROM missions WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Mission removed.', 'info')
    return redirect(url_for('admin_missions'))

@app.route('/admin/missions/<int:mid>/images/new', methods=['POST'])
@login_required
def admin_mission_image_new(mid):
    conn = get_db()
    mission = conn.execute("SELECT id FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('admin_missions'))
    image_count = conn.execute("SELECT COUNT(*) FROM mission_images WHERE mission_id=?", (mid,)).fetchone()[0]
    files = [f for f in request.files.getlist('photo') if f and f.filename]
    if not files:
        conn.close()
        flash('Please choose at least one image.', 'info')
        return redirect(url_for('admin_mission_edit', mid=mid))
    caption = request.form.get('caption', '').strip()
    next_sort = (conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM mission_images WHERE mission_id=?", (mid,)).fetchone()[0] or 0) + 1
    saved = 0
    for f in files:
        photo = _save_file_obj(f)
        if photo:
            conn.execute(
                "INSERT INTO mission_images (mission_id,photo,caption,sort_order) VALUES (?,?,?,?)",
                (mid, photo, caption, next_sort + saved)
            )
            saved += 1
    conn.commit()
    conn.close()
    flash(f'{saved} image{"s" if saved != 1 else ""} added!', 'success')
    return redirect(url_for('admin_mission_edit', mid=mid))

@app.route('/admin/missions/<int:mid>/images/<int:iid>/delete', methods=['POST'])
@login_required
def admin_mission_image_delete(mid, iid):
    conn = get_db()
    conn.execute("DELETE FROM mission_images WHERE id=? AND mission_id=?", (iid, mid))
    conn.commit()
    conn.close()
    flash('Mission image removed.', 'info')
    return redirect(url_for('admin_mission_edit', mid=mid))

# Beyond the Walls
@app.route('/admin/beyond-the-walls')
@login_required
def admin_beyond_walls():
    conn = get_db()
    items = get_beyond_wall_cards(conn)
    conn.close()
    return render_template('admin/beyond_walls.html', items=items)

@app.route('/admin/beyond-the-walls/new', methods=['GET', 'POST'])
@login_required
def admin_beyond_wall_new():
    if request.method == 'POST':
        cover = save_upload('cover_photo') or ''
        youtube_url = request.form.get('youtube_url', '').strip()
        youtube_video_id = extract_youtube_video_id(youtube_url)
        if youtube_url and not youtube_video_id:
            flash("Couldn't recognize that YouTube link — video not saved.", 'info')
        conn = get_db()
        conn.execute(
            "INSERT INTO beyond_walls (title,summary,body,cover_photo,sort_order,youtube_video_id) VALUES (?,?,?,?,?,?)",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'beyond_walls'),
                youtube_video_id,
            )
        )
        conn.commit()
        bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        flash('Saved! Add images below.', 'success')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    return render_template('admin/beyond_wall_form.html', item=None, images=[])

@app.route('/admin/beyond-the-walls/<int:bid>/edit', methods=['GET', 'POST'])
@login_required
def admin_beyond_wall_edit(bid):
    conn = get_db()
    item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('admin_beyond_walls'))
    if request.method == 'POST':
        cover = save_upload('cover_photo')
        if cover is None:
            cover = item['cover_photo']
        youtube_url = request.form.get('youtube_url', '').strip()
        youtube_video_id = extract_youtube_video_id(youtube_url) if youtube_url else ''
        if youtube_url and not youtube_video_id:
            flash("Couldn't recognize that YouTube link — video not saved.", 'info')
            youtube_video_id = item['youtube_video_id']
        conn.execute(
            "UPDATE beyond_walls SET title=?,summary=?,body=?,cover_photo=?,sort_order=?,youtube_video_id=? WHERE id=?",
            (
                request.form.get('title', '').strip(),
                request.form.get('summary', '').strip(),
                request.form.get('body', '').strip(),
                cover,
                form_sort_order() if form_sort_order() is not None else item['sort_order'],
                youtube_video_id,
                bid,
            )
        )
        conn.commit()
        flash('Updated!', 'success')
        item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    images = conn.execute(
        "SELECT * FROM beyond_wall_images WHERE beyond_id=? ORDER BY sort_order, id",
        (bid,)
    ).fetchall()
    conn.close()
    return render_template('admin/beyond_wall_form.html', item=item, images=images)

@app.route('/admin/beyond-the-walls/<int:bid>/delete', methods=['POST'])
@login_required
def admin_beyond_wall_delete(bid):
    conn = get_db()
    conn.execute("DELETE FROM beyond_walls WHERE id=?", (bid,))
    conn.commit()
    conn.close()
    flash('Removed.', 'info')
    return redirect(url_for('admin_beyond_walls'))

@app.route('/admin/beyond-the-walls/<int:bid>/images/new', methods=['POST'])
@login_required
def admin_beyond_wall_image_new(bid):
    conn = get_db()
    exists = conn.execute("SELECT id FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not exists:
        conn.close()
        return redirect(url_for('admin_beyond_walls'))
    image_count = conn.execute("SELECT COUNT(*) FROM beyond_wall_images WHERE beyond_id=?", (bid,)).fetchone()[0]
    files = [f for f in request.files.getlist('photo') if f and f.filename]
    if not files:
        conn.close()
        flash('Choose at least one image.', 'info')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    remaining = MAX_BEYOND_WALL_IMAGES - image_count
    if remaining <= 0:
        conn.close()
        flash(f'This story already has the max of {MAX_BEYOND_WALL_IMAGES} images.', 'info')
        return redirect(url_for('admin_beyond_wall_edit', bid=bid))
    if len(files) > remaining:
        flash(f'Only {remaining} more image{"s" if remaining != 1 else ""} fit under the {MAX_BEYOND_WALL_IMAGES}-image cap — the rest were skipped.', 'info')
        files = files[:remaining]
    caption = request.form.get('caption', '').strip()
    next_sort = (conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM beyond_wall_images WHERE beyond_id=?", (bid,)).fetchone()[0] or 0) + 1
    saved = 0
    for f in files:
        photo = _save_file_obj(f)
        if photo:
            conn.execute(
                "INSERT INTO beyond_wall_images (beyond_id,photo,caption,sort_order) VALUES (?,?,?,?)",
                (bid, photo, caption, next_sort + saved)
            )
            saved += 1
            conn.commit()  # commit per image so a slow/interrupted batch keeps whatever finished
    conn.close()
    flash(f'{saved} image{"s" if saved != 1 else ""} added!', 'success')
    return redirect(url_for('admin_beyond_wall_edit', bid=bid))

@app.route('/admin/beyond-the-walls/<int:bid>/images/<int:iid>/delete', methods=['POST'])
@login_required
def admin_beyond_wall_image_delete(bid, iid):
    conn = get_db()
    conn.execute("DELETE FROM beyond_wall_images WHERE id=? AND beyond_id=?", (iid, bid))
    conn.commit()
    conn.close()
    flash('Image removed.', 'info')
    return redirect(url_for('admin_beyond_wall_edit', bid=bid))

# Leaders
@app.route('/admin/leaders')
@login_required
def admin_leaders():
    conn=get_db(); l=conn.execute("SELECT * FROM leaders ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/leaders.html',leaders=l)

@app.route('/admin/leaders/new',methods=['GET','POST'])
@login_required
def admin_leader_new():
    if request.method=='POST':
        photo=save_upload('photo') or ''
        conn=get_db(); conn.execute("INSERT INTO leaders (name,role,bio,photo,sort_order) VALUES (?,?,?,?,?)",
            (request.form['name'].strip(),request.form['role'].strip(),request.form.get('bio','').strip(),photo,form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'leaders')))
        conn.commit(); conn.close(); flash('Leader added!','success'); return redirect(url_for('admin_leaders'))
    return render_template('admin/leader_form.html',leader=None)

@app.route('/admin/leaders/<int:lid>/edit',methods=['GET','POST'])
@login_required
def admin_leader_edit(lid):
    conn = get_db()
    l = conn.execute("SELECT * FROM leaders WHERE id=?",(lid,)).fetchone()
    if not l:
        conn.close()
        return redirect(url_for('admin_leaders'))
    if request.method == 'POST':
        try:
            photo = save_upload('photo')   # None if no new file
            if photo is None:
                photo = l['photo']         # keep existing photo
            conn.execute(
                "UPDATE leaders SET name=?,role=?,bio=?,photo=?,sort_order=? WHERE id=?",
                (request.form['name'].strip(), request.form['role'].strip(),
                 request.form.get('bio','').strip(), photo,
                 form_sort_order() if form_sort_order() is not None else l['sort_order'], lid)
            )
            conn.commit()
            flash('Leader updated!', 'success')
        except Exception as e:
            app.logger.error(f"leader_edit error: {e}")
            flash(f'Error saving: {e}', 'info')
        finally:
            conn.close()
        return redirect(url_for('admin_leaders'))
    conn.close()
    return render_template('admin/leader_form.html', leader=l)

@app.route('/admin/leaders/<int:lid>/delete',methods=['POST'])
@login_required
def admin_leader_delete(lid):
    conn=get_db(); conn.execute("DELETE FROM leaders WHERE id=?",(lid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_leaders'))

# Behind the Scene
@app.route('/admin/behind-the-scene')
@login_required
def admin_behind_scenes():
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scenes = conn.execute("SELECT * FROM behind_scenes ORDER BY sort_order, name").fetchall()
    counts = get_people_count_by_scene(conn)
    people = get_crew_people(conn)
    conn.close()
    return render_template('admin/behind_scenes.html', scenes=scenes, member_counts=counts, people=people)

@app.route('/admin/behind-the-scene/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_new():
    return redirect(url_for('admin_behind_scene_person_new'))

@app.route('/admin/behind-the-scene/<int:sid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_edit(sid):
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    if not scene:
        conn.close()
        return redirect(url_for('admin_behind_scenes'))
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = scene['photo']
        conn.execute(
            "UPDATE behind_scenes SET photo=? WHERE id=?",
            (
                photo,
                sid,
            )
        )
        conn.commit()
        flash("Oasis Crew ministry updated!", 'success')
        scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    members = get_people_for_scene(conn, sid)
    conn.close()
    return render_template('admin/behind_scene_form.html', scene=scene, members=members)

@app.route('/admin/behind-the-scene/<int:sid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_delete(sid):
    flash("Delete ministries from the Ministries admin section. Oasis Crew mirrors that list automatically.", 'info')
    return redirect(url_for('admin_ministries'))

@app.route('/admin/behind-the-scene/members/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_person_new():
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene_choices = get_scene_choices(conn)
    default_scene_id = request.args.get('scene_id', '').strip()
    default_scene_id = int(default_scene_id) if default_scene_id.isdigit() else None
    next_sort_order = next_available_sort_order(conn, 'behind_scene_people')
    if request.method == 'POST':
        photo = save_upload('photo') or ''
        conn.execute(
            "INSERT INTO behind_scene_people (name,photo,sort_order) VALUES (?,?,?)",
            (
                request.form['name'].strip(),
                photo,
                form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'behind_scene_people'),
            )
        )
        person_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        slots = assignment_slots_from_form(request.form)
        if not slots and default_scene_id:
            slots = [{'scene_id': default_scene_id, 'role': request.form.get('assignment_1_role', '').strip(), 'slot_order': 1}]
        replace_person_assignments(conn, person_id, slots)
        conn.commit()
        conn.close()
        flash('Member added!', 'success')
        return redirect(url_for('admin_behind_scenes'))
    conn.close()
    return render_template(
        'admin/behind_scene_person_form.html',
        scene=None,
        member=None,
        assignments=[],
        scene_choices=scene_choices,
        default_scene_id=default_scene_id,
        display_order=next_sort_order,
    )

@app.route('/admin/behind-the-scene/members/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_person_edit(mid):
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene_choices = get_scene_choices(conn)
    member, assignments = get_person_with_assignments(conn, mid)
    if not member:
        conn.close()
        return redirect(url_for('admin_behind_scenes'))
    if request.method == 'POST':
        photo = save_upload('photo')
        if photo is None:
            photo = member['photo']
        conn.execute(
            "UPDATE behind_scene_people SET name=?,photo=?,sort_order=? WHERE id=?",
            (
                request.form['name'].strip(),
                photo,
                form_sort_order() if form_sort_order() is not None else member['sort_order'],
                mid,
            )
        )
        slots = assignment_slots_from_form(request.form)
        replace_person_assignments(conn, mid, slots)
        conn.commit()
        conn.close()
        flash('Member updated!', 'success')
        return redirect(url_for('admin_behind_scenes'))
    conn.close()
    return render_template(
        'admin/behind_scene_person_form.html',
        scene=None,
        member=member,
        assignments=assignments,
        scene_choices=scene_choices,
        default_scene_id=None,
        display_order=member['sort_order'],
    )

@app.route('/admin/behind-the-scene/members/<int:mid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_person_delete(mid):
    conn = get_db()
    conn.execute("DELETE FROM behind_scene_people WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Member removed.', 'info')
    return redirect(url_for('admin_behind_scenes'))

@app.route('/admin/behind-the-scene/<int:sid>/members/new', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_member_new(sid):
    return redirect(url_for('admin_behind_scene_person_new', scene_id=sid))

@app.route('/admin/behind-the-scene/<int:sid>/members/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def admin_behind_scene_member_edit(sid, mid):
    return redirect(url_for('admin_behind_scene_person_edit', mid=mid))

@app.route('/admin/behind-the-scene/<int:sid>/members/<int:mid>/delete', methods=['POST'])
@login_required
def admin_behind_scene_member_delete(sid, mid):
    return redirect(url_for('admin_behind_scene_person_delete', mid=mid))

# Beliefs
@app.route('/admin/beliefs')
@login_required
def admin_beliefs():
    conn=get_db(); items=conn.execute("SELECT * FROM beliefs ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/beliefs.html',beliefs=items, values=[])

@app.route('/admin/beliefs/new',methods=['GET','POST'])
@login_required
def admin_belief_new():
    if request.method=='POST':
        conn=get_db(); conn.execute("INSERT INTO beliefs (title,body,scripture,sort_order) VALUES (?,?,?,?)",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'beliefs')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_beliefs'))
    return render_template('admin/belief_form.html',belief=None)

@app.route('/admin/beliefs/<int:bid>/edit',methods=['GET','POST'])
@login_required
def admin_belief_edit(bid):
    conn=get_db(); b=conn.execute("SELECT * FROM beliefs WHERE id=?",(bid,)).fetchone()
    if not b: conn.close(); return redirect(url_for('admin_beliefs'))
    if request.method=='POST':
        conn.execute("UPDATE beliefs SET title=?,body=?,scripture=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),form_sort_order() if form_sort_order() is not None else b['sort_order'],bid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_beliefs'))
    images = conn.execute("SELECT * FROM belief_images WHERE belief_id=? ORDER BY sort_order, id", (bid,)).fetchall()
    conn.close(); return render_template('admin/belief_form.html',belief=b,images=images)

@app.route('/admin/beliefs/<int:bid>/delete',methods=['POST'])
@login_required
def admin_belief_delete(bid):
    conn=get_db(); conn.execute("DELETE FROM beliefs WHERE id=?",(bid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_beliefs'))

@app.route('/admin/beliefs/<int:bid>/images/new',methods=['POST'])
@login_required
def admin_belief_image_new(bid):
    conn=get_db()
    image_count = conn.execute("SELECT COUNT(*) FROM belief_images WHERE belief_id=?", (bid,)).fetchone()[0]
    if image_count >= 10:
        conn.close(); flash('Each belief can have up to 10 images.', 'info'); return redirect(url_for('admin_belief_edit', bid=bid))
    photo = save_upload('photo')
    if not photo:
        conn.close(); flash('Choose an image first.', 'info'); return redirect(url_for('admin_belief_edit', bid=bid))
    conn.execute(
        "INSERT INTO belief_images (belief_id,photo,caption,sort_order) VALUES (?,?,?,?)",
        (bid, photo, request.form.get('caption','').strip(), form_sort_order() if form_sort_order() is not None else next_available_sort_order_for_parent(conn, 'belief_images', 'belief_id', bid))
    )
    conn.commit(); conn.close(); flash('Belief image added!', 'success'); return redirect(url_for('admin_belief_edit', bid=bid))

@app.route('/admin/beliefs/<int:bid>/images/<int:iid>/delete',methods=['POST'])
@login_required
def admin_belief_image_delete(bid, iid):
    conn=get_db(); conn.execute("DELETE FROM belief_images WHERE id=? AND belief_id=?", (iid, bid)); conn.commit(); conn.close()
    flash('Belief image removed.','info'); return redirect(url_for('admin_belief_edit', bid=bid))

@app.route('/admin/values')
@login_required
def admin_values():
    conn=get_db(); items=conn.execute("SELECT * FROM values_items ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/values.html',values=items)

@app.route('/admin/values/new',methods=['GET','POST'])
@login_required
def admin_value_new():
    if request.method=='POST':
        conn=get_db(); conn.execute("INSERT INTO values_items (title,body,scripture,sort_order) VALUES (?,?,?,?)",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'values_items')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_values'))
    return render_template('admin/value_form.html',value=None)

@app.route('/admin/values/<int:vid>/edit',methods=['GET','POST'])
@login_required
def admin_value_edit(vid):
    conn=get_db(); v=conn.execute("SELECT * FROM values_items WHERE id=?",(vid,)).fetchone()
    if not v: conn.close(); return redirect(url_for('admin_values'))
    if request.method=='POST':
        conn.execute("UPDATE values_items SET title=?,body=?,scripture=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(),request.form['body'].strip(),request.form.get('scripture','').strip(),form_sort_order() if form_sort_order() is not None else v['sort_order'],vid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_values'))
    images = conn.execute("SELECT * FROM value_images WHERE value_id=? ORDER BY sort_order, id", (vid,)).fetchall()
    conn.close(); return render_template('admin/value_form.html',value=v,images=images)

@app.route('/admin/values/<int:vid>/delete',methods=['POST'])
@login_required
def admin_value_delete(vid):
    conn=get_db(); conn.execute("DELETE FROM values_items WHERE id=?",(vid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_values'))

@app.route('/admin/values/<int:vid>/images/new',methods=['POST'])
@login_required
def admin_value_image_new(vid):
    conn=get_db()
    image_count = conn.execute("SELECT COUNT(*) FROM value_images WHERE value_id=?", (vid,)).fetchone()[0]
    if image_count >= 10:
        conn.close(); flash('Each value can have up to 10 images.', 'info'); return redirect(url_for('admin_value_edit', vid=vid))
    photo = save_upload('photo')
    if not photo:
        conn.close(); flash('Choose an image first.', 'info'); return redirect(url_for('admin_value_edit', vid=vid))
    conn.execute(
        "INSERT INTO value_images (value_id,photo,caption,sort_order) VALUES (?,?,?,?)",
        (vid, photo, request.form.get('caption','').strip(), form_sort_order() if form_sort_order() is not None else next_available_sort_order_for_parent(conn, 'value_images', 'value_id', vid))
    )
    conn.commit(); conn.close(); flash('Value image added!', 'success'); return redirect(url_for('admin_value_edit', vid=vid))

@app.route('/admin/values/<int:vid>/images/<int:iid>/delete',methods=['POST'])
@login_required
def admin_value_image_delete(vid, iid):
    conn=get_db(); conn.execute("DELETE FROM value_images WHERE id=? AND value_id=?", (iid, vid)); conn.commit(); conn.close()
    flash('Value image removed.','info'); return redirect(url_for('admin_value_edit', vid=vid))

# Ministries
@app.route('/admin/ministries')
@login_required
def admin_ministries():
    conn=get_db(); items=conn.execute("SELECT * FROM ministries ORDER BY sort_order").fetchall(); conn.close()
    return render_template('admin/ministries.html',ministries=items)

@app.route('/admin/ministries/new',methods=['GET','POST'])
@login_required
def admin_ministry_new():
    if request.method=='POST':
        conn=get_db(); photo = save_upload('photo')
        conn.execute("INSERT INTO ministries (name,description,url,icon,photo,sort_order) VALUES (?,?,?,?,?,?)",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('url','').strip(),request.form.get('icon','bi-people-fill').strip(),photo or '',form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'ministries')))
        conn.commit(); conn.close(); flash('Added!','success'); return redirect(url_for('admin_ministries'))
    return render_template('admin/ministry_form.html',ministry=None)

@app.route('/admin/ministries/<int:mid>/edit',methods=['GET','POST'])
@login_required
def admin_ministry_edit(mid):
    conn=get_db(); m=conn.execute("SELECT * FROM ministries WHERE id=?",(mid,)).fetchone()
    if not m: conn.close(); return redirect(url_for('admin_ministries'))
    if request.method=='POST':
        photo = save_upload('photo')
        if photo is None:
            photo = m['photo'] if 'photo' in m.keys() else ''
        conn.execute("UPDATE ministries SET name=?,description=?,url=?,icon=?,photo=?,sort_order=? WHERE id=?",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('url','').strip(),request.form.get('icon','bi-people-fill').strip(),photo or '',form_sort_order() if form_sort_order() is not None else m['sort_order'],mid))
        conn.commit(); conn.close(); flash('Updated!','success'); return redirect(url_for('admin_ministries'))
    conn.close(); return render_template('admin/ministry_form.html',ministry=m)

@app.route('/admin/ministries/<int:mid>/delete',methods=['POST'])
@login_required
def admin_ministry_delete(mid):
    conn=get_db(); conn.execute("DELETE FROM ministries WHERE id=?",(mid,)); conn.commit(); conn.close()
    flash('Removed.','info'); return redirect(url_for('admin_ministries'))

# Serve
@app.route('/admin/serve')
@login_required
def admin_serve():
    conn=get_db()
    cats=conn.execute("SELECT * FROM serve_categories ORDER BY sort_order").fetchall()
    roles=conn.execute("SELECT * FROM serve_roles ORDER BY sort_order").fetchall(); conn.close()
    grouped=[{'cat':c,'roles':[r for r in roles if r['category_id']==c['id']]} for c in cats]
    return render_template('admin/serve.html',grouped=grouped)

@app.route('/admin/serve/category/new',methods=['POST'])
@login_required
def admin_serve_cat_new():
    photo = save_upload('photo') or ''
    conn=get_db(); conn.execute("INSERT INTO serve_categories (name,description,icon,color,photo,sort_order) VALUES (?,?,?,?,?,?)",
        (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('icon','bi-people-fill').strip(),request.form.get('color','teal'),photo,form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'serve_categories')))
    conn.commit(); conn.close(); flash('Category added!','success'); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/category/<int:cid>/edit',methods=['GET','POST'])
@login_required
def admin_serve_cat_edit(cid):
    conn=get_db(); cat=conn.execute("SELECT * FROM serve_categories WHERE id=?",(cid,)).fetchone()
    if not cat: conn.close(); return redirect(url_for('admin_serve'))
    if request.method=='POST':
        photo = save_upload('photo')
        if photo is None:
            photo = cat['photo']
        conn.execute("UPDATE serve_categories SET name=?,description=?,icon=?,color=?,photo=?,sort_order=? WHERE id=?",
            (request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('icon','bi-people-fill').strip(),request.form.get('color','teal'),photo,form_sort_order() if form_sort_order() is not None else cat['sort_order'],cid))
        conn.commit(); conn.close(); flash('Category updated!','success'); return redirect(url_for('admin_serve'))
    conn.close(); return render_template('admin/serve_cat_form.html',cat=cat)

@app.route('/admin/serve/category/<int:cid>/delete',methods=['POST'])
@login_required
def admin_serve_cat_delete(cid):
    conn=get_db(); conn.execute("DELETE FROM serve_roles WHERE category_id=?",(cid,)); conn.execute("DELETE FROM serve_categories WHERE id=?",(cid,))
    conn.commit(); conn.close(); flash('Category deleted.','info'); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/role/new',methods=['POST'])
@login_required
def admin_serve_role_new():
    conn=get_db(); conn.execute("INSERT INTO serve_roles (category_id,label,sort_order) VALUES (?,?,?)",
        (int(request.form['category_id']),request.form['label'].strip(),form_sort_order() if form_sort_order() is not None else next_available_sort_order(conn, 'serve_roles')))
    conn.commit(); conn.close(); return redirect(url_for('admin_serve'))

@app.route('/admin/serve/role/<int:rid>/delete',methods=['POST'])
@login_required
def admin_serve_role_delete(rid):
    conn=get_db(); conn.execute("DELETE FROM serve_roles WHERE id=?",(rid,)); conn.commit(); conn.close()
    return redirect(url_for('admin_serve'))

# Events
@app.route('/admin/events')
@login_required
def admin_events():
    conn=get_db(); ev=conn.execute("SELECT * FROM events ORDER BY event_date ASC").fetchall(); conn.close()
    return render_template('admin/events.html',events=ev)

@app.route('/admin/events/new',methods=['GET','POST'])
@login_required
def admin_event_new():
    if request.method=='POST':
        rec = request.form.get('recurrence','none')
        rec_detail = ''
        if rec == 'weekly':
            positions = [p for p in ['1st','2nd','3rd','4th','last'] if request.form.get(f'pos_{p}')]
            weekday   = request.form.get('recurrence_weekday','Sunday')
            rec_detail = ','.join(positions) + '-' + weekday if positions else weekday
        conn=get_db(); conn.execute(
            "INSERT INTO events (title,description,event_date,event_time,location,signup_url,auto_delete,recurrence,recurrence_detail,sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request.form['title'].strip(), request.form.get('description','').strip(),
             request.form['event_date'], request.form.get('event_time','').strip(),
             request.form.get('location','').strip(), request.form.get('signup_url','').strip(),
             1 if request.form.get('auto_delete') else 0,
             rec, rec_detail,
             int_or(request.form.get('sort_order'), 0)))
        conn.commit(); conn.close(); flash('Event added!','success'); return redirect(url_for('admin_events'))
    return render_template('admin/event_form.html',event=None)

@app.route('/admin/events/<int:eid>/edit',methods=['GET','POST'])
@login_required
def admin_event_edit(eid):
    conn=get_db(); ev=conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone()
    if not ev: conn.close(); return redirect(url_for('admin_events'))
    if request.method=='POST':
        rec = request.form.get('recurrence','none')
        rec_detail = ''
        if rec == 'weekly':
            positions = [p for p in ['1st','2nd','3rd','4th','last'] if request.form.get(f'pos_{p}')]
            weekday   = request.form.get('recurrence_weekday','Sunday')
            rec_detail = ','.join(positions) + '-' + weekday if positions else weekday
        conn.execute(
            "UPDATE events SET title=?,description=?,event_date=?,event_time=?,location=?,signup_url=?,auto_delete=?,recurrence=?,recurrence_detail=?,sort_order=? WHERE id=?",
            (request.form['title'].strip(), request.form.get('description','').strip(),
             request.form['event_date'], request.form.get('event_time','').strip(),
             request.form.get('location','').strip(), request.form.get('signup_url','').strip(),
             1 if request.form.get('auto_delete') else 0,
             rec, rec_detail,
             int_or(request.form.get('sort_order'), 0), eid))
        conn.commit(); conn.close(); flash('Event updated!','success'); return redirect(url_for('admin_events'))
    conn.close(); return render_template('admin/event_form.html',event=ev)

@app.route('/admin/events/<int:eid>/delete',methods=['POST'])
@login_required
def admin_event_delete(eid):
    conn=get_db(); conn.execute("DELETE FROM events WHERE id=?",(eid,)); conn.commit(); conn.close()
    flash('Event removed.','info'); return redirect(url_for('admin_events'))



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
