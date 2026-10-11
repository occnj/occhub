/* Oasis Hub — client-side scroll animations
   Gentle fade-up for cards further down the page + hero parallax + tactile card presses.
   Self-contained: injects its own CSS. Skipped entirely for reduced-motion users.

   Nothing that is on screen when a page opens is ever hidden. Hiding it and fading it
   back in made every page arrive blank and then "flash" its content, which the slide-in
   page transition (transitions.css) made worse: the incoming page was captured empty.

   Safety rule for everything below: this file hides content before animating it
   in, so any path that hides MUST have a guaranteed path that un-hides. iOS does
   not run requestAnimationFrame while a page isn't being painted — which is the
   state a home-screen PWA is in behind the iOS launch screen, and after the app
   is suspended and restored from a snapshot. Without the failsafes here, those
   rAF callbacks never fire, nothing is ever revealed, and the app sits on a
   blank background indefinitely. */
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var ITEMS = [
    '.grid > *', '.stack > *', '.list > *',
    '.mission-card', '.leader-card', '.group-card', '.note-card',
    '.event-card', '.story-card', '.video-card', '.category',
    '.row', '.empty', '.info-block'
  ].join(',');
  var PRESSABLE = 'a.tile, .mission-card, .leader-card, .group-card, .note-card, .event-card, .story-card, .video-card';

  /* How long to wait for the animation to get going before assuming it never
     will and showing everything outright. A stalled reveal is invisible to the
     user, so these only ever fire on a broken load. */
  var RAF_FAILSAFE_MS = 1500;
  var IO_FAILSAFE_MS = 5000;

  var css = document.createElement('style');
  css.textContent =
    '.rv{opacity:0;transform:translateY(16px);}' +
    '.rv.rv-in{opacity:1;transform:none;' +
      'transition:opacity .45s ease,transform .45s cubic-bezier(.22,1,.36,1);}' +
    '.hero-content>*{will-change:transform;}' +
    '.hv{opacity:0;transform:translateY(26px);}' +
    '.hv.hv-in{opacity:1;transform:none;' +
      'transition:opacity .7s ease,transform .8s cubic-bezier(.22,1,.36,1);}' +
    PRESSABLE + '{transition:transform .25s cubic-bezier(.34,1.56,.64,1);}' +
    PRESSABLE.split(',').map(function (s) { return s + ':active'; }).join(',') +
      '{transform:scale(.965);}';
  document.head.appendChild(css);

  var started = false;   /* content has been hidden and the reveal scheduled */
  var painted = false;   /* the double-rAF actually ran, so the page is painting */
  var heroBits = [];
  var items = [];
  var io = null;

  /* Strip every hiding class outright. Idempotent, and safe to call at any
     point — an element that was mid-transition just snaps to its final state. */
  function revealAll() {
    painted = true;
    if (io) { io.disconnect(); io = null; }
    var all = heroBits.concat(items);
    all.forEach(function (el) {
      el.classList.remove('rv', 'rv-in', 'hv', 'hv-in');
      el.style.transitionDelay = '';
    });
  }

  /* Backstop for a dead IntersectionObserver: anything already on screen should
     have been revealed by now, so if it is still hidden the observer is not
     working. On a healthy load every one of these has already been unobserved
     and this is a no-op. */
  function sweep() {
    if (!started) return;
    var stuck = document.querySelectorAll('.rv:not(.rv-in)');
    for (var i = 0; i < stuck.length; i++) {
      var r = stuck[i].getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) stuck[i].classList.add('rv-in');
    }
  }

  /* Drop the hiding classes once the entrance transition has played, so the
     element is left with no leftover opacity/transform of ours. */
  function clearAfterTransition(el, hidden, shown) {
    el.addEventListener('transitionend', function done(e) {
      if (e.propertyName !== 'transform') return;
      el.classList.remove(hidden, shown);
      el.style.transitionDelay = '';
      el.removeEventListener('transitionend', done);
    });
  }

  function start() {
    if (started) return;
    started = true;

    /* The hero is the first thing on screen, so it is shown as-is (no entrance). */
    heroBits = [];

    /* Cards below the fold: hidden, then faded up as they scroll into view. Anything
       already visible on load is left alone. */
    var fold = window.innerHeight || document.documentElement.clientHeight;
    items = Array.prototype.slice.call(document.querySelectorAll(ITEMS))
      .filter(function (el) { return !el.closest('.sheet,.overlay,.card-overlay,.notice-overlay,.modal-overlay'); })
      .filter(function (el) { return el.getBoundingClientRect().top > fold; });
    items.forEach(function (el) { el.classList.add('rv'); });

    if ('IntersectionObserver' in window && items.length) {
      io = new IntersectionObserver(function (entries) {
        var i = 0;
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var el = en.target;
          el.style.transitionDelay = (i++ * 50) + 'ms';
          el.classList.add('rv-in');
          clearAfterTransition(el, 'rv', 'rv-in');
          if (io) io.unobserve(el);
        });
      }, { threshold: 0.06, rootMargin: '0px 0px -5% 0px' });
    }

    /* Double rAF so the hidden state is painted before transitions start,
       otherwise fast devices skip the animation entirely. */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        painted = true;
        heroBits.forEach(function (el, i) {
          el.style.transitionDelay = (i * 110) + 'ms';
          el.classList.add('hv-in');
          clearAfterTransition(el, 'hv', 'hv-in');
        });
        if (io) items.forEach(function (el) { io.observe(el); });
        else items.forEach(function (el) { el.classList.add('rv-in'); });
      });
    });

    /* If the rAFs never ran, the page was never painted and nothing above
       happened — show everything rather than leave a blank screen. */
    setTimeout(function () { if (!painted) revealAll(); }, RAF_FAILSAFE_MS);
    setTimeout(sweep, IO_FAILSAFE_MS);

    /* Scrolling is the other moment a stalled reveal shows up as a blank
       stretch of page. This runs even when an observer exists, because the
       failure seen before was an observer that was live but never fired; when
       it is working every on-screen item is already revealed and this is a
       no-op. Time-throttled rather than rAF-throttled, since a starved rAF is
       one of the things being guarded against. */
    var lastSweep = 0;
    window.addEventListener('scroll', function () {
      var now = Date.now();
      if (now - lastSweep < 250) return;
      lastSweep = now;
      sweep();
    }, { passive: true });

    parallax();
  }

  /* Hero parallax: inner content lags behind the scroll and fades,
     like a native app header. Outer hero box stays in flow. */
  function parallax() {
    var heroContent = document.querySelector('.hero .hero-content');
    var hero = document.querySelector('.hero');
    if (!heroContent || !hero) return;
    var ticking = false;
    window.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        var y = window.scrollY || window.pageYOffset;
        var h = hero.offsetHeight || 1;
        var p = Math.min(Math.max(y / h, 0), 1);
        heroContent.style.transform = 'translateY(' + (y * 0.28).toFixed(1) + 'px)';
        heroContent.style.opacity = (1 - p * 0.85).toFixed(3);
      });
    }, { passive: true });
  }

  function init() {
    /* Launched behind the iOS launch screen or otherwise not being painted:
       hiding now would rely on rAFs that will not fire. Wait for the page to
       actually become visible and animate from there instead. */
    if (document.visibilityState === 'hidden') return;
    start();
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible') return;
    if (!started) start();
    else if (!painted) revealAll();
    else sweep();
  });

  /* Restored from the back/forward cache or an iOS app snapshot: the reveal
     may have been frozen mid-flight, so make sure nothing is left hidden. */
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    if (!started) start();
    else revealAll();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* Page-slide direction for the View Transitions in transitions.css.
   Runs unconditionally (deferred, so before first paint): the incoming page
   must carry html.vt-back BEFORE the transition starts to slide right instead
   of left. Back is flagged two ways: a tap on a back button on the previous
   page, or a browser back/forward navigation (button or swipe gesture). */
(function () {
  var back = false;
  try {
    back = sessionStorage.getItem('vtdir') === 'back';
    sessionStorage.removeItem('vtdir');
  } catch (e) {}
  var nav = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
  if (nav && nav.type === 'back_forward') back = true;
  if (back) document.documentElement.classList.add('vt-back');

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    try {
      if (a.classList.contains('back-btn') || a.hasAttribute('data-back')) {
        sessionStorage.setItem('vtdir', 'back');
      } else {
        sessionStorage.removeItem('vtdir');
      }
    } catch (err) {}
  }, true);
})();
