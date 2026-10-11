"""Special Events page (/special-events) and its admin page (/admin/special-events)."""
from core import *
from special_events import *


def _send_special_event_email(se, req):
    name = f"{req['first_name']} {req['last_name']}".strip()
    rows = [('Life event(s)', ', '.join(req['services'])), ('Name', name), ('Email', req['email']),
            ('Phone', f"{req['phone']} ({req['phone_type']})")]
    details = ''.join(f'<b>{esc(k)}:</b> {esc(v)}<br>' for k, v in rows)
    notes = esc(req['message']).replace(chr(10), '<br>') or '—'
    body = f"""<html><body style="font-family:Arial;color:#333;line-height:1.7">
    <div style="background:#13677A;padding:20px;text-align:center"><h1 style="color:white;margin:0">Life Events Request</h1></div>
    <div style="padding:24px;border:1px solid #ddd;border-top:none">
    <p>{details}</p>
    <hr><h3 style="color:#13677A">Notes</h3>
    <p style="background:#f9f9f9;padding:14px;border-left:4px solid #13677A">{notes}</p>
    <p style="color:#888;font-size:12px">Also saved in the Hub admin under Special Events.</p></div></body></html>"""
    reply_to = req['email'] if EMAIL_RE.fullmatch(req['email'] or '') else None
    return send_email(f"Life Events Request: {one_line(', '.join(req['services']))} — {one_line(name)}",
                      se['recipients'], body, reply_to=reply_to)


@app.route('/special-events', methods=['GET', 'POST'])
def special_events():
    track('special_events')
    settings = all_settings()
    se = get_special_events(settings)
    form, error, sent = {}, None, False
    if request.method == 'POST':
        if se['mode'] != 'form':
            return redirect(url_for('special_events'))
        if form_rate_limited('special_events'):
            return render_template('special_events.html', se=se, settings=settings, form={'services': []}, sent=False,
                                   phone_types=PHONE_TYPES,
                                   error='Too many requests from this device. Please try again in a few minutes.'), 429
        offered = [s['name'] for s in se['services']]
        phone_type = request.form.get('phone_type', 'Mobile')
        form = {
            'services': [n for n in offered if n in request.form.getlist('services')],   # keeps the page's order
            'first_name': clip(request.form.get('first_name', '').strip(), 80),
            'last_name': clip(request.form.get('last_name', '').strip(), 80),
            'email': clip(request.form.get('email', '').strip(), 200),
            'phone': clip(request.form.get('phone', '').strip(), 40),
            'phone_type': phone_type if phone_type in PHONE_TYPES else 'Mobile',
            'message': clip(request.form.get('message', '').strip(), 5000),
        }
        if not form['services']:
            error = 'Please check at least one life event.'
        elif not (form['first_name'] and form['last_name']):
            error = 'Please enter your first and last name.'
        elif not EMAIL_RE.fullmatch(form['email']):
            error = 'Please enter a valid email address.'
        elif len(re.sub(r'\D', '', form['phone'])) < 7:
            error = 'Please enter a phone number.'
        if not error:
            conn = get_db()
            conn.execute(
                "INSERT INTO special_event_requests (submitted_at,services,first_name,last_name,email,phone,phone_type,message) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now().strftime('%Y-%m-%d %H:%M'), ', '.join(form['services']), form['first_name'],
                 form['last_name'], form['email'], form['phone'], form['phone_type'], form['message']))
            conn.commit()
            conn.close()
            _send_special_event_email(se, form)
            sent = True
    if not form:
        form = {'services': [request.args['service']] if request.args.get('service') else []}
    return render_template('special_events.html', se=se, settings=settings, form=form, phone_types=PHONE_TYPES,
                           error=error, sent=sent), (400 if error else 200)


@app.route('/admin/special-events', methods=['GET', 'POST'])
@login_required
def admin_special_events():
    if request.method == 'POST':
        recipients = parse_recipients(request.form.get('special_events_email', ''))
        if not recipients:
            flash('Please enter at least one valid email address for requests.', 'info')
            return redirect(url_for('admin_special_events'))
        values = {
            'special_events_title': request.form.get('special_events_title', '').strip() or 'Special Events',
            'special_events_description': request.form.get('special_events_description', '').strip(),
            'special_events_intro': request.form.get('special_events_intro', '').strip(),
            'special_events_email': ', '.join(recipients),
            'special_events_mode': 'link' if request.form.get('special_events_mode') == 'link' else 'form',
            'special_events_form_url': request.form.get('special_events_form_url', '').strip(),
            'special_events_info_url': request.form.get('special_events_info_url', '').strip(),
        }
        for slot in range(1, SPECIAL_EVENT_SLOTS + 1):
            values[se_key(slot, 'enabled')] = '1' if request.form.get(se_key(slot, 'enabled')) else '0'
            values[se_key(slot, 'name')] = request.form.get(se_key(slot, 'name'), '').strip()
            values[se_key(slot, 'icon')] = request.form.get(se_key(slot, 'icon'), '').strip() or 'bi-calendar-heart'
            values[se_key(slot, 'description')] = request.form.get(se_key(slot, 'description'), '').strip()
        conn = get_db()
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        conn.commit()
        conn.close()
        flash('Special Events saved!', 'success')
        return redirect(url_for('admin_special_events'))
    conn = get_db()
    requests_ = conn.execute("SELECT * FROM special_event_requests ORDER BY id DESC").fetchall()
    card = conn.execute("SELECT id, is_active FROM hub_cards WHERE slug='special-events'").fetchone()
    conn.close()
    return render_template('admin/special_events.html', settings=all_settings(), slots=range(1, SPECIAL_EVENT_SLOTS + 1),
                           se_key=se_key, requests=requests_, hub_card=card,
                           new_count=sum(1 for r in requests_ if (r['status'] or 'new') == 'new'))


@app.route('/admin/special-events/<int:rid>/status', methods=['POST'])
@login_required
def admin_special_event_status(rid):
    status = 'handled' if request.form.get('status') == 'handled' else 'new'
    conn = get_db()
    conn.execute("UPDATE special_event_requests SET status=? WHERE id=?", (status, rid))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_special_events') + '#requests')


@app.route('/admin/special-events/<int:rid>/delete', methods=['POST'])
@login_required
def admin_special_event_delete(rid):
    conn = get_db()
    conn.execute("DELETE FROM special_event_requests WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash('Request deleted.', 'info')
    return redirect(url_for('admin_special_events') + '#requests')
