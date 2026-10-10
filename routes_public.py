"""Public (visitor-facing) routes."""
from core import *
from youtube import *
from documents import *

# ── Public routes ─────────────────────────────────────────────────────────────

@app.route('/manifest.json')
def pwa_manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def pwa_service_worker():
    resp = app.send_static_file('sw.js')
    # Always revalidate so a new SW version deploys without waiting out the cache
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

@app.route('/')
def splash(): track('splash'); return render_template('splash.html')

@app.route('/gate')
def gate():
    track('gate')
    thank_you = None
    if request.args.get('connect') == '1':
        raw_name = (request.args.get('name') or '').strip()
        first_name = raw_name.split()[0] if raw_name else ''
        thank_you = f"Thank you{', ' + first_name if first_name else ''}. We will reach out soon."
    return render_template('gate.html', settings=all_settings(), thank_you=thank_you)

@app.route('/hub')
def hub():
    track('hub')
    hour=datetime.now().hour
    greeting="Good Morning" if hour<12 else "Good Afternoon" if hour<17 else "Good Evening"
    settings = all_settings()
    conn = get_db()
    latest_note = latest_sermon_note(conn)
    hub_cards = get_hub_cards(conn)
    conn.close()
    return render_template(
        'hub.html',
        greeting=greeting,
        date=datetime.now().strftime("%b %d, %Y").upper(),
        settings=settings,
        hub_notice=get_hub_notice(settings),
        get_involved=get_get_involved(settings),
        latest_note=latest_note,
        hub_cards=hub_cards,
    )

@app.route('/watch-sermon')
def watch_sermon():
    track('watch_sermon')
    settings = all_settings()
    return render_template('watch_sermon.html', settings=settings, videos=get_sermon_videos(settings))

@app.route('/sermon-notes')
def sermon_notes():
    track('sermon_notes')
    conn = get_db()
    notes = conn.execute("SELECT * FROM sermon_notes ORDER BY note_date DESC, id DESC").fetchall()
    latest = notes[0] if notes else None
    ns_counts = next_steps_counts(conn)
    conn.close()
    return render_template('sermon_notes.html', notes=notes, latest=latest,
                           ns_counts=ns_counts, settings=all_settings())

@app.route('/sermon-notes/<int:nid>')
def sermon_note_detail(nid):
    track('sermon_note_detail')
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    latest = latest_sermon_note(conn)
    ns_items = note_next_steps(conn, nid) if note else []
    conn.close()
    if not note:
        return redirect(url_for('sermon_notes'))
    return render_template('sermon_note_detail.html', note=note, latest=latest,
                           ns_items=ns_items, settings=all_settings())

@app.route('/sermon-notes/<int:nid>/next-steps/<int:fid>')
def sermon_next_steps(nid, fid):
    """Hand one Oasis Next Steps take-home document to the visitor's phone.
    ?view=1 opens it inline instead, which is the friendlier path on iOS where a
    forced download drops the file into Files rather than showing it."""
    conn = get_db()
    note = conn.execute("SELECT * FROM sermon_notes WHERE id=?", (nid,)).fetchone()
    item = conn.execute(
        "SELECT * FROM sermon_next_steps WHERE id=? AND note_id=?", (fid, nid)
    ).fetchone()
    conn.close()
    if not note or not item:
        return redirect(url_for('sermon_note_detail', nid=nid))
    fname = os.path.basename(row_value(item, 'file'))
    if not fname or not os.path.isfile(os.path.join(UPLOAD_FOLDER, fname)):
        app.logger.warning(f"sermon_next_steps: missing file for note {nid} item {fid}")
        return redirect(url_for('sermon_note_detail', nid=nid))
    inline = request.args.get('view') == '1'
    track('sermon_next_steps_view' if inline else 'sermon_next_steps_download')
    return send_from_directory(
        UPLOAD_FOLDER, fname,
        as_attachment=not inline,
        download_name=next_steps_download_name(note, item),
    )

@app.route('/sermon-notes/<int:nid>/next-steps')
def sermon_next_steps_first(nid):
    """Kept so links handed out before multi-file support still resolve."""
    conn = get_db()
    items = note_next_steps(conn, nid)
    conn.close()
    if not items:
        return redirect(url_for('sermon_note_detail', nid=nid))
    return redirect(url_for('sermon_next_steps', nid=nid, fid=items[0]['id'],
                            **({'view': '1'} if request.args.get('view') == '1' else {})))

@app.route('/beliefs')
def beliefs():
    track('beliefs')
    return render_template('beliefs_landing.html', settings=all_settings())

@app.route('/beliefs/statement')
def beliefs_statement():
    track('beliefs_statement')
    conn=get_db()
    items=conn.execute("SELECT * FROM beliefs ORDER BY sort_order").fetchall()
    images=conn.execute("SELECT * FROM belief_images ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template(
        'beliefs.html',
        beliefs=items,
        belief_images=gallery_images_by_parent(images, 'belief_id'),
        settings=all_settings()
    )

@app.route('/beliefs/values')
def beliefs_values():
    track('beliefs_values')
    conn=get_db()
    items=conn.execute("SELECT * FROM values_items ORDER BY sort_order").fetchall()
    images=conn.execute("SELECT * FROM value_images ORDER BY sort_order, id").fetchall()
    conn.close()
    return render_template(
        'values.html',
        values=items,
        value_images=gallery_images_by_parent(images, 'value_id'),
        settings=all_settings()
    )

@app.route('/ministries')
def ministries():
    # Only ministries with a description are public; serving teams synced from
    # Oasis Crew have empty descriptions and stay off this page.
    track('ministries'); conn=get_db(); items=conn.execute("SELECT * FROM ministries WHERE TRIM(COALESCE(description,''))!='' ORDER BY sort_order").fetchall(); conn.close()
    return render_template('ministries.html',ministries=items,settings=all_settings())

@app.route('/leadership')
def leadership():
    track('leadership'); conn=get_db(); leaders=conn.execute("SELECT * FROM leaders ORDER BY sort_order").fetchall(); conn.close()
    return render_template('leadership.html',leaders=leaders,settings=all_settings())

@app.route('/behind-the-scene')
def behind_scene():
    track('behind_the_scene')
    settings = all_settings()
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scenes = list(conn.execute("SELECT * FROM behind_scenes ORDER BY sort_order, name").fetchall())
    random.shuffle(scenes)
    counts = get_people_count_by_scene(conn)
    conn.close()
    return render_template('behind_scene.html', scenes=scenes, member_counts=counts, settings=settings)

@app.route('/behind-the-scene/<int:sid>')
def behind_scene_detail(sid):
    track('behind_the_scene_detail')
    conn = get_db()
    sync_behind_scenes_with_ministries(conn)
    scene = conn.execute("SELECT * FROM behind_scenes WHERE id=?", (sid,)).fetchone()
    if not scene:
        conn.close()
        return redirect(url_for('behind_scene'))
    members = get_people_for_scene(conn, sid)
    conn.close()
    return render_template('behind_scene_detail.html', scene=scene, members=members)

@app.route('/mission')
def missions():
    track('missions')
    conn = get_db()
    cards = get_mission_cards(conn)
    conn.close()
    return render_template('missions.html', missions=cards, settings=all_settings())

@app.route('/mission/<int:mid>')
def mission_detail(mid):
    track('mission_detail')
    conn = get_db()
    mission = conn.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    if not mission:
        conn.close()
        return redirect(url_for('missions'))
    images = conn.execute(
        "SELECT * FROM mission_images WHERE mission_id=? ORDER BY sort_order, id",
        (mid,)
    ).fetchall()
    conn.close()
    return render_template('mission_detail.html', mission=mission, images=images, settings=all_settings())

@app.route('/beyond-the-walls')
def beyond_the_walls():
    track('beyond_the_walls')
    conn = get_db()
    cards = get_beyond_wall_cards(conn)
    conn.close()
    return render_template('beyond_walls.html', items=cards, settings=all_settings())

@app.route('/beyond-the-walls/<int:bid>')
def beyond_the_walls_detail(bid):
    track('beyond_the_walls_detail')
    conn = get_db()
    item = conn.execute("SELECT * FROM beyond_walls WHERE id=?", (bid,)).fetchone()
    if not item:
        conn.close()
        return redirect(url_for('beyond_the_walls'))
    images = conn.execute(
        "SELECT * FROM beyond_wall_images WHERE beyond_id=? ORDER BY sort_order, id",
        (bid,)
    ).fetchall()
    conn.close()
    return render_template('beyond_walls_detail.html', item=item, images=images, settings=all_settings())

@app.route('/hub-cards/<int:cid>')
def hub_card_group(cid):
    track('hub_card_group')
    conn = get_db()
    parent = conn.execute("SELECT * FROM hub_cards WHERE id=? AND is_active=1", (cid,)).fetchone()
    if not parent:
        conn.close()
        return redirect(url_for('hub'))
    if parent['slug'] == 'mission':
        conn.close()
        return redirect(url_for('missions'))
    if parent['slug'] == 'beliefs-values':
        conn.close()
        return redirect(url_for('beliefs'))
    if parent['slug'] == 'beyond-the-walls':
        conn.close()
        return redirect(url_for('beyond_the_walls'))
    children = get_hub_cards(conn, parent_id=cid)
    conn.close()
    return render_template('hub_card_group.html', parent=parent, cards=children, settings=all_settings())

@app.route('/serve')
def serve():
    track('serve'); conn=get_db()
    cats=conn.execute("SELECT * FROM serve_categories ORDER BY sort_order").fetchall()
    roles=conn.execute("SELECT * FROM serve_roles ORDER BY sort_order").fetchall(); conn.close()
    grouped=[{'cat':c,'roles':[r for r in roles if r['category_id']==c['id']]} for c in cats]
    return render_template('serve.html',grouped=grouped,settings=all_settings())

def expand_recurring(events, today_str, months_ahead=6):
    """Return a merged list of one-time + expanded recurring occurrences."""
    from datetime import timedelta
    import calendar as cal_mod
    today  = date.fromisoformat(today_str)
    cutoff = date(today.year + (today.month + months_ahead - 1) // 12,
                  (today.month + months_ahead - 1) % 12 + 1, 1)

    result = []
    for e in events:
        rec = (e['recurrence'] or 'none').strip()
        if rec == 'none':
            result.append(dict(e))
            continue
        # Build synthetic occurrences from today up to cutoff
        detail = (e['recurrence_detail'] or '').strip()  # e.g. "1,3-Thursday" or "last-Saturday"
        cur = today.replace(day=1)
        while cur <= cutoff:
            year, month = cur.year, cur.month
            last_day = cal_mod.monthrange(year, month)[1]
            days_in_month = [date(year, month, d) for d in range(1, last_day+1)]
            occurrences = []
            try:
                # detail format: "1st,3rd-Thursday" or "last-Saturday"
                dash_idx = detail.rfind('-')
                weekday_name = detail[dash_idx+1:].strip().capitalize()
                pos_str      = detail[:dash_idx].strip() if dash_idx > 0 else ''
                weekdays = {'Monday':0,'Tuesday':1,'Wednesday':2,'Thursday':3,
                            'Friday':4,'Saturday':5,'Sunday':6}
                wd = weekdays.get(weekday_name, 0)
                matching = [d for d in days_in_month if d.weekday() == wd]
                pos_map = {'1st':0,'2nd':1,'3rd':2,'4th':3,'last':-1}
                pos_list = [p.strip() for p in pos_str.split(',') if p.strip()] if pos_str else list(pos_map.keys())
                for pos in pos_list:
                    if pos == 'last' and matching:
                        occurrences.append(matching[-1])
                    elif pos in pos_map and pos != 'last':
                        idx = pos_map[pos]
                        if idx < len(matching):
                            occurrences.append(matching[idx])
            except Exception:
                pass
            for occ_date in occurrences:
                if occ_date >= today:
                    row = dict(e)
                    row['event_date'] = occ_date.isoformat()
                    row['id'] = f"rec_{e['id']}_{occ_date.isoformat()}"
                    result.append(row)
            # next month
            if month == 12: cur = date(year+1, 1, 1)
            else:           cur = date(year, month+1, 1)
    result.sort(key=lambda x: (str(x['event_date']), str(x.get('event_time',''))))
    return result

@app.route('/calendar')
def calendar():
    track('calendar')
    conn = get_db()
    today_str = date.today().isoformat()
    # Finished one-time events marked auto-delete are removed by maintenance.py (at start-up
    # and from the nightly job), never as a side effect of someone viewing this page.
    events = conn.execute("SELECT * FROM events ORDER BY event_date ASC, event_time ASC").fetchall()
    conn.close()
    all_events = expand_recurring(events, today_str)
    upcoming   = [e for e in all_events if str(e['event_date']) >= today_str]
    conn2 = get_db()
    past_raw = conn2.execute(
        "SELECT * FROM events WHERE recurrence='none' AND auto_delete=0 AND event_date < ? ORDER BY event_date DESC LIMIT 20",
        (today_str,)
    ).fetchall()
    conn2.close()
    past_rows = [dict(r) for r in past_raw]
    return render_template('calendar.html', upcoming=upcoming, past=past_rows, today=today_str, settings=all_settings())

@app.route('/prayer',methods=['GET','POST'])
def prayer():
    track('prayer'); sent=False
    if request.method=='POST' and form_rate_limited('prayer'):
        return render_template('prayer.html',sent=False,settings=all_settings()), 429
    if request.method=='POST':
        fn=clip(request.form.get('full_name','').strip(),120); em=clip(request.form.get('email','').strip(),200)
        ph=clip(request.form.get('phone','').strip(),40); rt=clip(request.form.get('request_type','Personal'),60)
        msg=clip(request.form.get('message','').strip(),5000); priv=1 if request.form.get('is_private') else 0
        conn=get_db()
        conn.execute("INSERT INTO prayer_requests (submitted_at,full_name,email,phone,request_type,message,is_private) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,em,ph,rt,msg,priv))
        conn.commit(); conn.close()
        priv_note="<p style='color:#c0392b;'><b>⚠ PRIVATE — Pastoral team only.</b></p>" if priv else ""
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#13677A;padding:20px;text-align:center"><h1 style="color:white;margin:0">Prayer Request</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">{priv_note}
        <p><b>Name:</b> {esc(fn) or '—'}<br><b>Email:</b> {esc(em) or '—'}<br><b>Phone:</b> {esc(ph) or '—'}<br><b>Type:</b> {esc(rt)}</p>
        <hr><h3 style="color:#13677A">Request</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #13677A">{esc(msg).replace(chr(10), '<br>')}</p></div></body></html>"""
        send_email(f"Prayer Request: {one_line(fn)}",get_setting('prayer_email','Oasis@OasisNJ.net'),html)
        sent=True
    return render_template('prayer.html',sent=sent,settings=all_settings())

@app.route('/feedback',methods=['GET','POST'])
def feedback():
    track('feedback'); sent=False
    if request.method=='POST' and form_rate_limited('feedback'):
        return render_template('feedback.html',sent=False,settings=all_settings()), 429
    if request.method=='POST':
        fn=clip(request.form.get('full_name','').strip(),120)
        try: rating=max(0,min(5,int(request.form.get('rating','0') or 0)))
        except ValueError: rating=0
        msg=clip(request.form.get('message','').strip(),5000)
        if msg or rating:
            conn=get_db()
            conn.execute("INSERT INTO app_feedback (submitted_at,full_name,rating,message) VALUES (?,?,?,?)",
                (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,rating,msg))
            conn.commit(); conn.close()
            # No email notification — app feedback/ratings stay in the admin panel only.
        sent=True
    return render_template('feedback.html',sent=sent,settings=all_settings())

@app.route('/connect',methods=['GET','POST'])
def connect():
    track('connect')
    if request.method=='POST' and form_rate_limited('connect'):
        return render_template('connect.html',settings=all_settings()), 429
    if request.method=='POST':
        fn=clip(request.form.get('full_name','').strip(),120); em=clip(request.form.get('email','').strip(),200)
        ph=clip(request.form.get('phone','').strip(),40); addr=clip(request.form.get('address','').strip(),300)
        mar=clip(request.form.get('marital',''),60); gen=clip(request.form.get('gender',''),60)
        age=clip(request.form.get('age_group',''),60); sts=clip(request.form.get('member_status',''),60)
        ref=clip(request.form.get('referral','').strip(),200)
        kg=clip(', '.join(request.form.getlist('kg')),500); fh=clip(', '.join(request.form.getlist('fh')),500); md=clip(', '.join(request.form.getlist('md')),500)
        sa=clip(request.form.get('serve_area',''),200); pr=clip(request.form.get('message','').strip(),5000)
        conn=get_db()
        conn.execute("INSERT INTO submissions (submitted_at,full_name,email,phone,address,gender,age_group,marital,member_status,referral,know_god,find_hope,make_diff,serve_area,prayer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M'),fn,em,ph,addr,gen,age,mar,sts,ref,kg,fh,md,sa,pr))
        conn.commit(); conn.close()
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#F2541B;padding:20px;text-align:center"><h1 style="color:white;margin:0">New Connect Card</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">
        <p><b>Name:</b> {esc(fn)}<br><b>Email:</b> {esc(em)}<br><b>Phone:</b> {esc(ph)}<br><b>Address:</b> {esc(addr)}<br>
        <b>Gender:</b> {esc(gen)} | <b>Age:</b> {esc(age)}<br><b>Marital:</b> {esc(mar)} | <b>Status:</b> {esc(sts)}<br><b>Referral:</b> {esc(ref)}</p>
        <hr><h3 style="color:#13677A">Know God</h3><p>{esc(kg) or 'None'}</p>
        <h3 style="color:#13677A">Find Hope</h3><p>{esc(fh) or 'None'}</p>
        <h3 style="color:#13677A">Make a Difference</h3><p>{esc(md) or 'None'}{f'<br><b>Serve Area:</b> {esc(sa)}' if sa else ''}</p>
        <hr><h3 style="color:#13677A">Prayer / Comments</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #F2541B">{esc(pr).replace(chr(10), '<br>') or '—'}</p>
        </div></body></html>"""
        send_email(f"Connect Card: {one_line(fn)}",get_setting('connect_email','media@oasisnj.net'),html)
        return redirect(url_for('gate', connect='1', name=fn))
    return render_template('connect.html',settings=all_settings())

@app.route('/contact',methods=['GET','POST'])
def contact():
    track('contact'); sent=False
    if request.method=='POST' and form_rate_limited('contact'):
        return render_template('contact.html',sent=False,error=None,settings=all_settings()), 429
    if request.method=='POST':
        fn=clip(request.form.get('first_name','').strip(),100); ln=clip(request.form.get('last_name','').strip(),100)
        em=clip(request.form.get('email','').strip(),200); ph=clip(request.form.get('phone','').strip(),40)
        msg=clip(request.form.get('message','').strip(),5000)
        html=f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
        <div style="background:#13677A;padding:20px;text-align:center"><h1 style="color:white;margin:0">Message from Oasis Hub</h1></div>
        <div style="padding:24px;border:1px solid #ddd;border-top:none">
        <p><b>Name:</b> {esc(fn)} {esc(ln)}<br><b>Email:</b> {esc(em) or '—'}<br><b>Phone:</b> {esc(ph) or '—'}</p>
        <hr><h3 style="color:#13677A">Message</h3>
        <p style="background:#f9f9f9;padding:14px;border-left:4px solid #13677A">{esc(msg).replace(chr(10), '<br>')}</p></div></body></html>"""
        # reply_to only when it looks like one address, so it can't be used to inject headers or extra recipients
        reply_to = em if re.fullmatch(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+', em) else None
        send_email(f"Message from {one_line(fn)} {one_line(ln)}",get_setting('contact_email','Oasis@OasisNJ.net'),html,reply_to=reply_to)
        sent=True
    return render_template('contact.html',sent=sent,error=None,settings=all_settings())

@app.route('/about')
def about(): track('about'); return render_template('about.html',settings=all_settings())

@app.route('/social')
def social(): track('social'); return render_template('social.html',settings=all_settings())



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
