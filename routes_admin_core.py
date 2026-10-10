"""Admin: login, dashboard, site settings, page headers, watch-sermon settings."""
from core import *
from youtube import *
from documents import *

# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route('/admin/login',methods=['GET','POST'])
def admin_login():
    if session.get('admin_logged_in'): return redirect(url_for('admin_dashboard'))
    error=None
    if request.method=='POST':
        ip = client_ip()
        allowed, wait = _check_rate_limit(ip)
        if not allowed:
            error = f'Too many failed attempts. Try again in {wait // 60 + 1} minute(s).'
            return render_template('admin/login.html', error=error)
        conn=get_db()
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        u=conn.execute("SELECT * FROM admin_users WHERE username=?",(username,)).fetchone()
        if u and verify_password(u['password'], password):
            ensure_admin_password_hash(conn, u, password)
            conn.close()
            _clear_login_attempts(ip)
            session.permanent=True; session['admin_logged_in']=True
            session['admin_user']=u['username']; session['admin_role']=u['role']
            return redirect(url_for('admin_dashboard'))
        conn.close()
        _record_failed_login(ip)
        error='Invalid username or password.'
    return render_template('admin/login.html',error=error)

@app.route('/admin/logout')
def admin_logout(): session.clear(); return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    conn=get_db()
    analytics = get_analytics_snapshot(conn)
    st={
        'leaders':conn.execute("SELECT COUNT(*) FROM leaders").fetchone()[0],
        'beliefs':conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0],
        'ministries':conn.execute("SELECT COUNT(*) FROM ministries").fetchone()[0],
        'submissions':conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        'prayers':conn.execute("SELECT COUNT(*) FROM prayer_requests").fetchone()[0],
        'events':conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        'views_today': analytics['views_today'],
        'views_total': analytics['total_views'],
        'sessions_today': analytics['sessions_today'],
    }
    recent=conn.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT 5").fetchall()
    top_pages=conn.execute("SELECT page,COUNT(*) cnt FROM analytics GROUP BY page ORDER BY cnt DESC LIMIT 8").fetchall()
    conn.close()
    return render_template('admin/dashboard.html',st=st,recent=recent,top_pages=top_pages)

@app.route('/admin/settings',methods=['GET','POST'])
@login_required
def admin_settings():
    if request.method=='POST':
        conn=get_db()
        for k,v in request.form.items():
            if k == 'csrf_token':
                continue
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",(k,v.strip()))
        logo=save_upload('logo_file')
        if logo: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('logo_path',?)",(logo,))
        hero=save_upload('about_hero_file')
        if hero: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('about_hero',?)",(hero,))
        custom_bg=save_upload('custom_bg_file')
        if custom_bg: conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('ui_background_image',?)",(custom_bg,))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('primary_cta_enabled',?)",
                     ('1' if request.form.get('primary_cta_enabled') else '0',))
        conn.commit(); conn.close(); flash('Settings saved!','success')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html',settings=all_settings())

@app.route('/admin/page-headers', methods=['GET', 'POST'])
@login_required
def admin_page_headers():
    if request.method == 'POST':
        conn = get_db()
        for key in [
            'about_page_description',
            'feedback_page_description',
            'beliefs_page_description',
            'values_page_description',
            'calendar_page_description',
            'connect_page_description',
            'contact_page_description',
            'leadership_page_description',
            'mission_page_description',
            'beyond_walls_page_description',
            'ministries_page_description',
            'prayer_page_description',
            'serve_page_description',
            'social_page_description',
            'watch_page_description',
            'sermon_notes_page_description',
            'crew_page_description',
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (key, request.form.get(key, '').strip())
            )
        conn.commit()
        conn.close()
        flash('Page header descriptions saved!', 'success')
        return redirect(url_for('admin_page_headers'))
    return render_template('admin/page_headers.html', settings=all_settings())

@app.route('/admin/watch-sermons', methods=['GET', 'POST'])
@login_required
def admin_watch_sermons():
    if request.method == 'POST':
        conn = get_db()
        channel_url = request.form.get('sermon_channel_url', '').strip()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
            ('sermon_channel_url', channel_url)
        )
        for slot in range(1, 11):
            title = request.form.get(f'sermon_video_{slot}_title', '').strip()
            url = request.form.get(f'sermon_video_{slot}_url', '').strip()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (f'sermon_video_{slot}_title', title)
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (f'sermon_video_{slot}_url', url)
            )
        conn.commit()
        conn.close()
        flash('Watch sermons updated!', 'success')
        return redirect(url_for('admin_watch_sermons'))
    return render_template('admin/watch_sermons.html', settings=all_settings(), videos=get_sermon_videos())



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]
