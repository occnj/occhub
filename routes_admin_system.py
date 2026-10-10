"""Admin: submissions, prayers, feedback, analytics, security, users, password."""
from core import *
from youtube import *
from documents import *
from maintenance import *

# Submissions + Prayers
@app.route('/admin/submissions')
@login_required
def admin_submissions():
    conn=get_db(); s=conn.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall(); conn.close()
    return render_template('admin/submissions.html',subs=s)

@app.route('/admin/submissions/<int:sid>')
@login_required
def admin_submission_detail(sid):
    conn=get_db(); s=conn.execute("SELECT * FROM submissions WHERE id=?",(sid,)).fetchone(); conn.close()
    if not s: return redirect(url_for('admin_submissions'))
    return render_template('admin/submission_detail.html',sub=s)

@app.route('/admin/prayers')
@login_required
def admin_prayers():
    conn=get_db(); p=conn.execute("SELECT * FROM prayer_requests ORDER BY id DESC").fetchall(); conn.close()
    return render_template('admin/prayers.html',prayers=p)

@app.route('/admin/feedback')
@login_required
def admin_feedback():
    conn=get_db()
    fb=conn.execute("SELECT * FROM app_feedback ORDER BY id DESC").fetchall()
    stats=conn.execute("SELECT ROUND(AVG(rating),1) avg_rating, COUNT(*) rated FROM app_feedback WHERE rating>0").fetchone()
    conn.close()
    return render_template('admin/feedback.html',feedback=fb,avg_rating=stats['avg_rating'] or 0,rated=stats['rated'],total=len(fb))

@app.route('/admin/feedback/<int:fid>/delete',methods=['POST'])
@login_required
def admin_feedback_delete(fid):
    conn=get_db(); conn.execute("DELETE FROM app_feedback WHERE id=?",(fid,)); conn.commit(); conn.close()
    flash('Feedback entry deleted.','success')
    return redirect(url_for('admin_feedback'))

# Analytics
@app.route('/admin/analytics/clear', methods=['POST'])
@login_required
def admin_analytics_clear():
    conn = get_db()
    conn.execute("DELETE FROM analytics")
    conn.commit()
    conn.close()
    flash('Analytics data cleared.', 'success')
    return redirect(url_for('admin_analytics'))

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    conn=get_db()
    snapshot = get_analytics_snapshot(conn)
    by_page=conn.execute("SELECT page,COUNT(*) cnt FROM analytics GROUP BY page ORDER BY cnt DESC").fetchall()
    by_day=conn.execute("SELECT substr(ts,1,10) day,COUNT(*) cnt FROM analytics GROUP BY day ORDER BY day DESC LIMIT 30").fetchall()
    by_device=conn.execute("""
        SELECT sid,
               COUNT(*) views,
               COUNT(DISTINCT page) pages,
               MIN(ts) first_seen,
               MAX(ts) last_seen,
               (SELECT ua FROM analytics a3 WHERE a3.sid=a1.sid ORDER BY a3.ts DESC LIMIT 1) ua
        FROM analytics a1
        GROUP BY sid
        ORDER BY last_seen DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return render_template(
        'admin/analytics.html',
        total=snapshot['total_views'],
        today=snapshot['views_today'],
        week=snapshot['views_week'],
        sessions=snapshot['sessions_total'],
        sessions_today=snapshot['sessions_today'],
        sessions_week=snapshot['sessions_week'],
        by_page=by_page,
        by_day=by_day,
        by_device=by_device,
    )

@app.route('/admin/analytics/device/<sid>')
@login_required
def admin_analytics_device(sid):
    conn=get_db()
    rows=conn.execute(
        "SELECT ts,page,ua FROM analytics WHERE sid=? ORDER BY ts DESC LIMIT 300",
        (sid,)
    ).fetchall()
    conn.close()
    if not rows:
        flash('No analytics data found for that device.', 'error')
        return redirect(url_for('admin_analytics'))
    return render_template('admin/analytics_device.html', sid=sid, rows=rows)

@app.route('/admin/security')
@superadmin_required
def admin_security():
    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    banned = conn.execute(
        "SELECT ip,banned_at,expires_at,reason FROM banned_ips WHERE expires_at > ? ORDER BY banned_at DESC",
        (now,)
    ).fetchall()
    conn.close()
    return render_template('admin/security.html', banned=banned, ban_days=BAN_DAYS)

@app.route('/admin/security/unban', methods=['POST'])
@superadmin_required
def admin_security_unban():
    ip = request.form.get('ip', '').strip()
    if ip:
        conn = get_db()
        conn.execute("DELETE FROM banned_ips WHERE ip=?", (ip,))
        conn.commit()
        conn.close()
        flash(f'{ip} unbanned.', 'success')
    return redirect(url_for('admin_security'))

# Users
@app.route('/admin/users')
@superadmin_required
def admin_users():
    conn=get_db(); u=conn.execute("SELECT * FROM admin_users ORDER BY id").fetchall(); conn.close()
    return render_template('admin/users.html',users=u)

@app.route('/admin/users/new',methods=['GET','POST'])
@superadmin_required
def admin_user_new():
    if request.method=='POST':
        uname=request.form.get('username','').strip(); pw=request.form.get('password','').strip()
        role=request.form.get('role','editor')
        if not uname or len(pw)<10: flash('Username required and password must be at least 10 characters.','info')
        else:
            conn=get_db()
            try:
                conn.execute("INSERT INTO admin_users (username,password,role) VALUES (?,?,?)",(uname,hash_password(pw),role))
                conn.commit(); flash('User created!','success')
            except sqlite3.IntegrityError: flash('Username already taken.','info')
            conn.close()
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html',user=None)

@app.route('/admin/users/<int:uid>/delete',methods=['POST'])
@superadmin_required
def admin_user_delete(uid):
    if uid==1: flash("Can't delete the primary admin.",'info')
    else:
        conn=get_db(); conn.execute("DELETE FROM admin_users WHERE id=?",(uid,)); conn.commit(); conn.close()
        flash('User removed.','info')
    return redirect(url_for('admin_users'))

@app.route('/admin/password',methods=['GET','POST'])
@login_required
def admin_password():
    if request.method=='POST':
        pw=request.form.get('new_password','').strip()
        current=request.form.get('current_password','')
        conn=get_db()
        u=conn.execute("SELECT * FROM admin_users WHERE username=?",(session['admin_user'],)).fetchone()
        if not u or not verify_password(u['password'], current): flash('Current password is incorrect.','info')
        elif len(pw)<10: flash('New password must be at least 10 characters.','info')
        else:
            conn.execute("UPDATE admin_users SET password=? WHERE username=?",(hash_password(pw),session['admin_user'])); conn.commit()
            flash('Password updated!','success')
        conn.close()
    return render_template('admin/password.html')



# Everything above is shared with the route modules via `from <module> import *`.
__all__ = [n for n in dir() if not n.startswith('__')]


# Storage (unused uploads)
@app.route('/admin/storage')
@superadmin_required
def admin_storage():
    return render_template('admin/storage.html', info=scan_uploads())

@app.route('/admin/storage/clean', methods=['POST'])
@superadmin_required
def admin_storage_clean():
    removed, freed, refused = delete_unused_uploads()
    if refused:
        flash(refused, 'info')
    else:
        flash(f'Removed {removed} unused file(s), freed {human_size(freed)}.', 'success')
    return redirect(url_for('admin_storage'))
