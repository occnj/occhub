/* Oasis Hub — client-side scroll animations
   Springy staggered card reveals + hero parallax + tactile card presses.
   Self-contained: injects its own CSS. Skipped entirely for reduced-motion users. */
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var ITEMS = [
    '.grid > *', '.stack > *', '.list > *',
    '.mission-card', '.leader-card', '.group-card', '.note-card',
    '.event-card', '.story-card', '.video-card', '.category',
    '.row', '.empty', '.info-block'
  ].join(',');
  var PRESSABLE = 'a.tile, .mission-card, .leader-card, .group-card, .note-card, .event-card, .story-card, .video-card';

  var css = document.createElement('style');
  css.textContent =
    '.rv{opacity:0;transform:translateY(42px) scale(.96);}' +
    '.rv.rv-in{opacity:1;transform:none;' +
      'transition:opacity .6s ease,transform .7s cubic-bezier(.34,1.3,.64,1);}' +
    '.hero-content>*{will-change:transform;}' +
    '.hv{opacity:0;transform:translateY(26px);}' +
    '.hv.hv-in{opacity:1;transform:none;' +
      'transition:opacity .7s ease,transform .8s cubic-bezier(.22,1,.36,1);}' +
    PRESSABLE + '{transition:transform .25s cubic-bezier(.34,1.56,.64,1);}' +
    PRESSABLE.split(',').map(function (s) { return s + ':active'; }).join(',') +
      '{transform:scale(.965);}';
  document.head.appendChild(css);

  function init() {
    /* Hero entrance: tag, title, copy cascade up on load */
    var heroBits = document.querySelectorAll('.hero-content > *');
    heroBits.forEach(function (el) { el.classList.add('hv'); });

    /* Cards: hidden, then revealed with stagger as they enter the viewport */
    var items = Array.prototype.slice.call(document.querySelectorAll(ITEMS))
      .filter(function (el) { return !el.closest('.sheet,.overlay,.card-overlay,.notice-overlay'); });
    items.forEach(function (el) { el.classList.add('rv'); });

    var io = null;
    if ('IntersectionObserver' in window && items.length) {
      io = new IntersectionObserver(function (entries) {
        var i = 0;
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var el = en.target;
          el.style.transitionDelay = (i++ * 80) + 'ms';
          el.classList.add('rv-in');
          el.addEventListener('transitionend', function done(e) {
            if (e.propertyName !== 'transform') return;
            el.classList.remove('rv', 'rv-in');
            el.style.transitionDelay = '';
            el.removeEventListener('transitionend', done);
          });
          io.unobserve(el);
        });
      }, { threshold: 0.06, rootMargin: '0px 0px -5% 0px' });
    }

    /* Double rAF so the hidden state is painted before transitions start,
       otherwise fast devices skip the animation entirely. */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        heroBits.forEach(function (el, i) {
          el.style.transitionDelay = (i * 110) + 'ms';
          el.classList.add('hv-in');
          el.addEventListener('transitionend', function done(e) {
            if (e.propertyName !== 'transform') return;
            el.classList.remove('hv', 'hv-in');
            el.style.transitionDelay = '';
            el.removeEventListener('transitionend', done);
          });
        });
        if (io) items.forEach(function (el) { io.observe(el); });
        else items.forEach(function (el) { el.classList.add('rv-in'); });
      });
    });

    /* Hero parallax: inner content lags behind the scroll and fades,
       like a native app header. Outer hero box stays in flow. */
    var heroContent = document.querySelector('.hero .hero-content');
    var hero = document.querySelector('.hero');
    if (heroContent && hero) {
      var ticking = false;
      var onScroll = function () {
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
      };
      window.addEventListener('scroll', onScroll, { passive: true });
    }
  }

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
